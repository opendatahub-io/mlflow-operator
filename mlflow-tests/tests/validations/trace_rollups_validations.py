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
    assert state["cronjob"].metadata.uid, "Trace-rollup CronJob UID was not captured"
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
    assert state.get("additional_start_time_ms", 0) // MS_PER_DAY == (
        state["start_time_ms"] // MS_PER_DAY
    ), "Additional traces were not seeded on the existing UTC day"
    expected_passes = state.get("expected_rollup_passes") or []
    assert len(expected_passes) == 2
    assert expected_passes[0]["trace_count"] == TRACE_COUNT
    assert expected_passes[1]["trace_count"] == TRACE_COUNT + ADDITIONAL_TRACE_COUNT


def validate_trace_rollup_job_completed(test_context: TestContext) -> None:
    """Validate that a one-off SQL rollup Job from the CronJob template completed."""
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
    """Compare each day's SQL rollup with the traces seeded on that day."""
    validate_no_error(test_context)
    state = test_context.trace_rollups_state
    expected = {
        day: (len(latencies), sum(latencies), max(latencies), min(latencies))
        for day, latencies in state["trace_latencies_by_day"].items()
    }
    observed = state.get("observed_sql_rollups")
    logger.info(
        "Comparing SQL trace-rollup rows: observed=%s expected=%s",
        observed,
        expected,
    )
    assert observed == expected, (
        f"Daily SQL trace-rollup rows were {observed}, expected {expected}"
    )


def validate_trace_rollup_api_metrics(test_context: TestContext) -> None:
    """Compare public trace metrics with the SQL rollups and seeded traces."""
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
    restored_sql_rows = state.get("restored_sql_rollups")
    restored_count_values = state.get("restored_count_metrics")
    restored_latency_values = state.get("restored_latency_metrics")
    assert restored_sql_rows == state.get("observed_sql_rollups"), (
        "SQL trace-rollup rows were not restored after the public metrics probe: "
        f"{restored_sql_rows} != {state.get('observed_sql_rollups')}"
    )
    assert restored_count_values == count_values, (
        "Public trace count did not return to its pre-probe value: "
        f"{restored_count_values} != {count_values}"
    )
    assert restored_latency_values == latency_values, (
        "Public trace latency did not return to its pre-probe value: "
        f"{restored_latency_values} != {latency_values}"
    )
    sql_rows = state["observed_sql_rollups"].values()
    assert sql_rows, f"No observed SQL rollup rows for pass {pass_index + 1}"
    daily_counts, daily_latency_sums, daily_latency_maxima, daily_latency_minima = zip(
        *sql_rows, strict=True
    )
    sql_trace_count = sum(daily_counts)
    assert sql_trace_count > 0, f"No traces in the SQL rollup rows for pass {pass_index + 1}"
    sql_latency_values = {
        "AVG": pytest.approx(sum(daily_latency_sums) / sql_trace_count),
        "MAX": max(daily_latency_maxima),
        "MIN": min(daily_latency_minima),
    }
    assert count_values == {"COUNT": sql_trace_count}, (
        f"Pass {pass_index + 1}: public trace count was {count_values}, "
        f"expected {sql_trace_count} from the SQL rollup rows"
    )
    assert latency_values == sql_latency_values, (
        f"Pass {pass_index + 1}: public latency was {latency_values}, "
        f"expected {sql_latency_values} from the SQL rollup rows"
    )
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
    traces_by_id = {trace.info.trace_id: trace for trace in traces}
    observed_ids = set(traces_by_id)
    expected_ids = set(state.get("trace_ids") or [])
    expected_span_ids_by_trace_id = state.get("expected_span_ids_by_trace_id") or {}
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
    assert set(expected_span_ids_by_trace_id) == expected_ids, (
        "Expected root and child span IDs were not captured for every seeded trace"
    )
    for trace_id in expected_ids:
        expected_span_ids = set(expected_span_ids_by_trace_id[trace_id])
        assert len(expected_span_ids) == 2, (
            f"Expected distinct root and child span IDs for trace {trace_id}, "
            f"got {expected_span_ids}"
        )
        trace = traces_by_id[trace_id]
        spans = trace.data.spans if trace.data is not None else []
        observed_span_ids = {span.span_id for span in spans or []}
        missing_span_ids = expected_span_ids - observed_span_ids
        assert not missing_span_ids, (
            f"Trace {trace_id} is missing seeded root/child span IDs "
            f"{missing_span_ids}; observed {observed_span_ids}"
        )
    passes = state.get("trace_status_passes") or []
    if len(passes) >= 2:
        initial_statuses = passes[0]
        latest_statuses = passes[-1]
        assert initial_statuses.items() <= latest_statuses.items(), (
            "The incremental rollup removed or changed an initially seeded trace: "
            f"initial={initial_statuses}, latest={latest_statuses}"
        )
