"""Validate backfilled values, row idempotency, and deployment invariants."""

from __future__ import annotations

import json
import logging

import pytest
from kubernetes import client

from ..shared import TestContext
from ..actions.trace_prepopulation_actions import (
    PREDECESSOR_SCHEMA_REVISION,
    TRACE_COUNT_ADDITIONAL,
    TRACE_COUNT_INITIAL,
    TRACE_MODEL_NAME,
    TRACE_MODEL_PROVIDER,
    _analytics_column_exists,
    _cr_snapshot,
    _database_query,
    _query_revision,
    _sql_in_bindings,
    _span_row_states,
    _trace_row_states,
)
from .validation_utils import validate_no_error

logger = logging.getLogger(__name__)


def _state(test_context: TestContext) -> dict:
    state = getattr(test_context, "prepopulation_state", None)
    if not state:
        pytest.fail("prepopulation_state was not prepared by the actions")
    return state


def _core_api() -> client.CoreV1Api:
    return client.CoreV1Api()


def _assert_cr_unchanged(state: dict) -> None:
    before = state["cr_before"]
    after = _cr_snapshot()
    if before != after:
        pytest.fail(
            "MLflow CR state changed during the prepopulation exercise "
            f"(before: {before}, after: {after})"
        )


def _assert_api_probes_succeeded(state: dict, run_key: str) -> None:
    errors = state["api_errors"].get(run_key, [])
    if errors:
        pytest.fail(
            f"MLflow API probes failed for the '{run_key}' "
            f"prepopulation run: {len(errors)} failed probes "
            f"(first error type: {errors[0]})"
        )


def _assert_no_migration_overlap(state: dict, run_key: str) -> None:
    overlapping = state["migration_overlap"].get(run_key, [])
    if overlapping:
        pytest.fail(
            f"Operator migration Job(s) overlapped the '{run_key}' prepopulation "
            f"run: {sorted(overlapping)}"
        )


def _assert_revision_unchanged(state: dict) -> None:
    revision = _query_revision(_core_api(), state)
    if revision != PREDECESSOR_SCHEMA_REVISION:
        pytest.fail(
            "Alembic revision changed during prepopulation: expected "
            f"{PREDECESSOR_SCHEMA_REVISION!r}, found {revision!r}. Prepopulation "
            "must not advance the schema revision."
        )


def validate_prepopulation_experiment_created(test_context: TestContext) -> None:
    """Validate admin-created state without requiring a test-user identity."""
    validate_no_error(test_context)
    assert test_context.active_experiment_id, "Prepopulation experiment was not created"
    assert test_context.active_experiment_id in test_context.experiments_to_delete, (
        "Prepopulation experiment was not recorded for cleanup"
    )


def validate_trace_prepopulation_prepared(test_context: TestContext) -> None:
    """Deployment wiring is Secret-backed PostgreSQL at the predecessor revision."""
    state = _state(test_context)

    if not state["secret_name"] or not state["secret_key"]:
        pytest.fail("Primary backend Secret reference was not resolved")
    if state["revision_before"] != PREDECESSOR_SCHEMA_REVISION:
        pytest.fail(
            f"Pre-run revision {state['revision_before']!r} is not the predecessor "
            f"revision {PREDECESSOR_SCHEMA_REVISION!r}"
        )
    if any(state["trace_info_columns_before"].values()) or any(
        state["spans_columns_before"].values()
    ):
        pytest.fail(
            "Trace analytics columns already exist at the predecessor schema; the "
            "predecessor deployment is not at the expected pre-migration state"
        )
    # Steady-state preconditions: the exercise must start from a deployment
    # that has finished migrating for its current generation, with no forced
    # migration queued behind it.
    if state["cr_before"]["force_migrate_pending"]:
        pytest.fail(
            "A mlflow.opendatahub.io/force-migrate request is pending on the "
            "MLflow CR; let it settle before running the prepopulation exercise"
        )
    migration = state["cr_before"]["migration_condition"]
    if (
        migration is None
        or migration["status"] != "True"
        or migration["observed_generation"] != state["cr_before"]["generation"]
    ):
        pytest.fail(
            "The MLflow CR has not completed migration for its current "
            "generation; prepopulation must start from a steady deployment"
        )
    _assert_cr_unchanged(state)


def validate_initial_prepopulation_traces_seeded(test_context: TestContext) -> None:
    """The first trace batch is visible through the live API."""
    state = _state(test_context)
    payloads = state["payloads"]["initial"]
    if len(payloads) != TRACE_COUNT_INITIAL:
        pytest.fail(
            f"Expected {TRACE_COUNT_INITIAL} seeded traces, found {len(payloads)}"
        )


def _assert_trace_names(core_api: client.CoreV1Api, state: dict, batch: str) -> None:
    payloads = state["payloads"][batch]
    expected = {payload["trace_id"]: payload["trace_name"] for payload in payloads}
    placeholders, params = _sql_in_bindings(list(expected))
    actual = json.loads(
        _database_query(
            core_api,
            state,
            "SELECT json_object_agg(request_id, trace_name) FROM trace_info "
            "WHERE request_id IN (" + placeholders + ")",
            params=params,
        )
    )
    if actual != expected:
        pytest.fail(
            f"Backfilled trace names do not match the {batch} trace IDs and names"
        )


def _assert_span_models(core_api: client.CoreV1Api, state: dict, batch: str) -> None:
    payloads = state["payloads"][batch]
    placeholders, params = _sql_in_bindings([p["trace_id"] for p in payloads])
    correct_spans = _database_query(
        core_api,
        state,
        "SELECT trace_id || ':' || span_id FROM spans WHERE trace_id IN ("
        + placeholders
        + ") AND model_name = :'model' AND model_provider = :'provider'",
        params={
            **params,
            "model": TRACE_MODEL_NAME,
            "provider": TRACE_MODEL_PROVIDER,
        },
    ).splitlines()
    expected_spans = {
        f"{payload['trace_id']}:{payload[key]}"
        for payload in payloads
        for key in ("root_span_id", "child_span_id")
    }
    if not expected_spans <= set(correct_spans):
        pytest.fail(
            "Seeded spans are missing or have incorrect model_name/model_provider values "
            f"in the {batch} batch"
        )


def _assessment_xmin(core_api: client.CoreV1Api, state: dict) -> str:
    xmin = _database_query(
        core_api,
        state,
        "SELECT a.xmin::text FROM assessments a JOIN trace_info t "
        "ON a.trace_id = t.request_id WHERE a.assessment_id = :'assessment_id' "
        "AND t.request_id = :'trace_id' AND a.aggregate_value = 0.75 "
        "AND a.is_numeric_value IS TRUE AND a.experiment_id = t.experiment_id "
        "AND a.trace_timestamp_ms = t.timestamp_ms",
        params={
            "assessment_id": state["assessment_id"],
            "trace_id": state["payloads"]["initial"][0]["trace_id"],
        },
    )
    if not xmin.isdecimal():
        pytest.fail("Seeded numeric assessment is missing or incorrectly backfilled")
    return xmin


def validate_first_prepopulation_job(test_context: TestContext) -> None:
    """First run backfills seeded analytics values without touching the CR."""
    state = _state(test_context)
    core_api = _core_api()

    parsed = state["parsed_output"]["first"]
    traces_stats = parsed["stats"]["Traces"]
    spans_stats = parsed["stats"]["Spans"]

    # Required counts are based on the trace IDs this test owns, never on
    # global table counts: the deployment stays live, so unrelated writers
    # may delete rows at any time, and a deleted unrelated row must not turn
    # a correct run into a failure. Every seeded (owned) row must be
    # corrected on the first run — the analytics columns start absent, so
    # trace_name and the span model fields are NULL until this Job
    # populates them — and the per-ID value checks below carry that proof.
    if traces_stats["updated"] < TRACE_COUNT_INITIAL:
        pytest.fail(
            f"First prepopulation run updated {traces_stats['updated']} trace rows "
            f"but {TRACE_COUNT_INITIAL} seeded rows required backfill"
        )
    if spans_stats["updated"] < 2 * TRACE_COUNT_INITIAL:
        pytest.fail(
            f"First prepopulation run updated {spans_stats['updated']} span rows "
            f"but {2 * TRACE_COUNT_INITIAL} seeded spans carried model attributes"
        )
    if "Traces" not in parsed["progress_entities"]:
        pytest.fail("First prepopulation run reported no Traces progress output")

    for column, existed in state["trace_info_columns_before"].items():
        if existed:
            continue
        if not _analytics_column_exists(core_api, state, "trace_info", column):
            pytest.fail(
                f"trace_info.{column} was not added by the prepopulation run"
            )
    for column, existed in state["spans_columns_before"].items():
        if existed:
            continue
        if not _analytics_column_exists(core_api, state, "spans", column):
            pytest.fail(f"spans.{column} was not added by the prepopulation run")

    _assert_trace_names(core_api, state, "initial")
    _assert_span_models(core_api, state, "initial")
    state["assessment_xmin_after_first_run"] = _assessment_xmin(core_api, state)

    # Capture row-change indicators for the rerun proof.
    state["xmin_after_first_run"] = _trace_row_states(
        core_api,
        state,
        [payload["trace_id"] for payload in state["payloads"]["initial"]],
    )
    state["span_xmin_after_first_run"] = _span_row_states(
        core_api, state, state["payloads"]["initial"]
    )

    _assert_revision_unchanged(state)
    _assert_cr_unchanged(state)
    _assert_api_probes_succeeded(state, "first")
    _assert_no_migration_overlap(state, "first")


def validate_additional_prepopulation_traces_seeded(
    test_context: TestContext,
) -> None:
    """The second trace batch is visible through the live API."""
    state = _state(test_context)
    payloads = state["payloads"]["additional"]
    if len(payloads) != TRACE_COUNT_ADDITIONAL:
        pytest.fail(
            f"Expected {TRACE_COUNT_ADDITIONAL} additional traces, found "
            f"{len(payloads)}"
        )


def validate_second_prepopulation_job(test_context: TestContext) -> None:
    """Rerun skips already-correct rows and only updates newly written rows."""
    state = _state(test_context)
    core_api = _core_api()

    parsed = state["parsed_output"]["second"]
    traces_stats = parsed["stats"]["Traces"]

    _assert_trace_names(core_api, state, "additional")
    _assert_span_models(core_api, state, "additional")
    if _assessment_xmin(core_api, state) != state["assessment_xmin_after_first_run"]:
        pytest.fail("Already-correct assessment changed during the rerun (xmin changed)")

    # Row-level idempotency, tolerant of other writers. The second run must
    # correct at least the rows the test itself created between runs; exact
    # global counter equality is deliberately not asserted, because rows
    # written by concurrent writers between the runs are also legitimately
    # corrected by the second run. The authoritative skipping evidence is the
    # complete per-ID xmin comparison below, not the global counters.
    if traces_stats["updated"] < TRACE_COUNT_ADDITIONAL:
        pytest.fail(
            f"Second prepopulation run updated {traces_stats['updated']} trace rows, "
            f"fewer than the {TRACE_COUNT_ADDITIONAL} rows created between runs; "
            "newly written rows were not corrected"
        )
    # The scan happened at run time, so bound it by the rows this test owns
    # and knows existed when the run started: a global count taken before
    # the run can include unrelated rows that were deleted before the scan,
    # which would fail a correct run.
    if traces_stats["scanned"] < TRACE_COUNT_INITIAL + TRACE_COUNT_ADDITIONAL:
        pytest.fail(
            f"Second prepopulation run scanned {traces_stats['scanned']} trace rows, "
            f"fewer than the {TRACE_COUNT_INITIAL + TRACE_COUNT_ADDITIONAL} rows "
            "seeded by the test"
        )

    # Authoritative row-level proof: rows corrected by the first run are not
    # rewritten by the second (PostgreSQL xmin changes on every update).
    xmin_after_second = _trace_row_states(
        core_api,
        state,
        [payload["trace_id"] for payload in state["payloads"]["initial"]],
    )
    changed_rows = sorted(
        trace_id
        for trace_id, xmin in xmin_after_second.items()
        if state["xmin_after_first_run"].get(trace_id) != xmin
    )
    if changed_rows:
        pytest.fail(
            f"{len(changed_rows)} already-correct trace rows were rewritten by the "
            "second prepopulation run (xmin changed)"
        )

    span_xmin_after_second = _span_row_states(
        core_api, state, state["payloads"]["initial"]
    )
    if span_xmin_after_second != state["span_xmin_after_first_run"]:
        pytest.fail(
            "Already-correct span rows changed during the second prepopulation run "
            "(xmin changed)"
        )

    _assert_revision_unchanged(state)
    _assert_cr_unchanged(state)
    _assert_api_probes_succeeded(state, "second")
    _assert_no_migration_overlap(state, "second")
