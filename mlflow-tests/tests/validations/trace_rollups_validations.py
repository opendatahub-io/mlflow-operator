"""Validations for the live SQL trace-rollup smoke flow."""

from __future__ import annotations

import logging

import pytest

from ..actions.trace_rollups_actions import (
    ADDITIONAL_TRACE_COUNT,
    DB_CREDENTIALS_SECRET_NAME,
    MS_PER_DAY,
    TRACE_COUNT,
)
from ..shared import TestContext
from .validation_utils import validate_no_error

logger = logging.getLogger(__name__)


def validate_trace_rollups_ready(test_context: TestContext) -> None:
    """Validate that the harness backend and rollup CronJob are ready."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    assert state.get("namespace"), "Trace-rollup namespace was not captured"
    assert state.get("database"), "Harness PostgreSQL database was not captured"
    assert state.get("database_pod"), "Harness PostgreSQL pod was not captured"
    assert state.get("cronjob_uid"), "Trace-rollup CronJob UID was not captured"
    assert state.get("backend_secret_name") == DB_CREDENTIALS_SECRET_NAME
    assert state.get("cronjob_schedule") == state.get("expected_schedule")
    assert state.get("cronjob_time_zone") == state.get("expected_time_zone")


def validate_trace_rollup_experiment_created(test_context: TestContext) -> None:
    """Validate the experiment created for the admin-authenticated smoke flow."""
    validate_no_error(test_context)
    experiment_id = test_context.active_experiment_id
    assert experiment_id, "Trace-rollup smoke experiment was not created"
    assert experiment_id in test_context.experiments_to_delete, (
        "Trace-rollup smoke experiment was not recorded for cleanup"
    )


def validate_trace_rollup_traces_seeded(test_context: TestContext) -> None:
    """Validate that all seeded backdated traces became searchable."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    trace_ids = state.get("trace_ids") or []
    assert len(trace_ids) == TRACE_COUNT, (
        f"Expected {TRACE_COUNT} seeded traces, got {len(trace_ids)}"
    )
    assert state.get("experiment_id") == test_context.active_experiment_id
    assert state.get("traces_ready_at"), "Trace visibility time was not captured"
    assert len(set(trace_ids)) == TRACE_COUNT, "Seeded trace IDs are not unique"


def validate_additional_trace_rollup_traces_seeded(test_context: TestContext) -> None:
    """Validate that new traces were added to the existing UTC rollup day."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    initial_ids = set(state.get("trace_ids_before_additional_seed") or [])
    additional_ids = state.get("additional_trace_ids") or []
    all_trace_ids = state.get("trace_ids") or []
    assert len(initial_ids) == TRACE_COUNT
    assert len(additional_ids) == ADDITIONAL_TRACE_COUNT, (
        f"Expected {ADDITIONAL_TRACE_COUNT} additional traces, got {len(additional_ids)}"
    )
    assert len(all_trace_ids) == TRACE_COUNT + ADDITIONAL_TRACE_COUNT
    assert len(set(all_trace_ids)) == len(all_trace_ids), "Seeded trace IDs are not unique"
    assert not initial_ids.intersection(additional_ids), (
        "Additional trace IDs overlap with the initial batch"
    )
    assert state.get("additional_traces_ready_at"), (
        "Additional trace visibility time was not captured"
    )
    assert state.get("additional_start_time_ms", 0) // MS_PER_DAY == (
        state["start_time_ms"] // MS_PER_DAY
    ), "Additional traces were not seeded on the existing UTC day"
    expected_passes = state.get("expected_rollup_passes") or []
    assert len(expected_passes) == 2
    assert expected_passes[0]["trace_count"] == TRACE_COUNT
    assert expected_passes[1]["trace_count"] == TRACE_COUNT + ADDITIONAL_TRACE_COUNT


def validate_trace_rollup_job_completed(test_context: TestContext) -> None:
    """Validate that a CronJob-created SQL rollup Job completed."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    jobs = state.get("rollup_job_names") or []
    assert state.get("rollup_passes") == 1
    assert jobs and jobs[0], "No completed first-pass trace-rollup Job was recorded"


def validate_trace_rollup_incremental_job_completed(test_context: TestContext) -> None:
    """Validate that a second, distinct Job completed after adding traces."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    jobs = state.get("rollup_job_names") or []
    assert state.get("rollup_passes") == 2
    assert len(jobs) == 2 and jobs[1], (
        f"Expected two completed rollup Jobs, got {jobs}"
    )
    assert jobs[1] != jobs[0], f"Second rollup reused the first Job {jobs[0]!r}"


def validate_trace_rollup_rows(test_context: TestContext) -> None:
    """Compare the SQL rollup rows with the known trace count and latencies."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    expected_passes = state.get("expected_rollup_passes") or []
    pass_index = len(state.get("sql_rollup_passes") or []) - 1
    assert 0 <= pass_index < len(expected_passes), (
        f"No expected SQL rollup values for pass {pass_index + 1}"
    )
    expected = expected_passes[pass_index]
    expected_values = (
        expected["trace_count"],
        expected["latency_sum"],
        expected["latency_max"],
        expected["latency_min"],
    )
    observed = state.get("observed_sql_rollups")
    logger.info(
        "Comparing SQL trace-rollup rows: observed=%s expected=%s",
        observed,
        expected_values,
    )
    assert observed == expected_values, (
        f"SQL trace-rollup rows were {observed}, expected {expected_values}"
    )
    passes = state.get("sql_rollup_passes") or []
    if len(passes) >= 2:
        assert passes[-1] != passes[0], (
            "SQL rollup rows did not include the additional traces: "
            f"{passes[0]} -> {passes[-1]}"
        )


def validate_trace_rollup_api_metrics(test_context: TestContext) -> None:
    """Compare public trace metrics with the known trace count and latencies."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    expected_passes = state.get("expected_rollup_passes") or []
    pass_index = len(state.get("api_metric_passes") or []) - 1
    assert 0 <= pass_index < len(expected_passes), (
        f"No expected public metrics for pass {pass_index + 1}"
    )
    expected = expected_passes[pass_index]
    count_values = state.get("observed_count_metrics")
    latency_values = state.get("observed_latency_metrics")
    expected_latency_values = {
        "AVG": pytest.approx(expected["latency_sum"] / expected["trace_count"]),
        "MAX": expected["latency_max"],
        "MIN": expected["latency_min"],
    }
    logger.info(
        "Comparing public trace metrics: count=%s latency=%s",
        count_values,
        latency_values,
    )
    assert count_values == {"COUNT": expected["trace_count"]}
    assert latency_values == expected_latency_values
    passes = state.get("api_metric_passes") or []
    if len(passes) >= 2:
        assert passes[-1] != passes[0], (
            "Public trace metrics did not include the additional traces: "
            f"{passes[0]} -> {passes[-1]}"
        )


def validate_trace_rollup_traces_readable(test_context: TestContext) -> None:
    """Validate that rollup processing left every source trace readable."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    traces = state.get("observed_traces") or []
    observed_ids = {trace.info.trace_id for trace in traces}
    expected_ids = set(state.get("trace_ids") or [])
    statuses = [trace.info.status for trace in traces]
    logger.info(
        "Validating %d source traces after rollup; statuses=%s",
        len(traces),
        statuses,
    )
    assert observed_ids == expected_ids, (
        f"Readable trace IDs were {observed_ids}, expected {expected_ids}"
    )
    assert len(traces) == state["expected"]["trace_count"]
    assert all(status == "OK" for status in statuses), (
        f"Source traces changed status after rollup: {statuses}"
    )
    passes = state.get("trace_status_passes") or []
    if len(passes) >= 2:
        initial_statuses = passes[0]
        latest_statuses = passes[-1]
        assert initial_statuses.items() <= latest_statuses.items(), (
            "The incremental rollup removed or changed an initially seeded trace: "
            f"initial={initial_statuses}, latest={latest_statuses}"
        )
