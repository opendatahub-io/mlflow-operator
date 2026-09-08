"""Validation functions for live trace-archival smoke coverage."""

from __future__ import annotations

import logging
import time

from mlflow.tracing.constant import SpansLocation, TraceTagKey

from ..actions.trace_archival_actions import ARCHIVAL_PREFIX
from ..shared import TestContext
from .validation_utils import validate_no_error

logger = logging.getLogger(__name__)


def _assert_trace_payloads(observed_traces, expected_payloads: list[dict]) -> None:
    for payload in expected_payloads:
        trace = observed_traces.get(payload["trace_id"])
        assert trace is not None, f"Trace {payload['trace_id']} was not found after archival"
        assert trace.data.spans, f"Trace {payload['trace_id']} has no persisted spans"
        expected_root_span_id = payload["spans"][0].span_id
        root_span = next(
            (span for span in trace.data.spans if span.span_id == expected_root_span_id),
            None,
        )
        assert root_span is not None, (
            f"Trace {payload['trace_id']} does not contain root span {expected_root_span_id}"
        )
        assert root_span.name == payload["trace_name"], (
            f"Expected root span '{payload['trace_name']}', got '{root_span.name}'"
        )
        assert root_span.inputs.get("message") == payload["message"], (
            f"Expected input message '{payload['message']}', got '{root_span.inputs.get('message')}'"
        )
        assert root_span.outputs.get("result") == payload["result"], (
            f"Expected output result '{payload['result']}', got '{root_span.outputs.get('result')}'"
        )


def _assert_spans_location(
    observed_traces, expected_payloads: list[dict], location: str
) -> None:
    for payload in expected_payloads:
        tags = dict(observed_traces[payload["trace_id"]].info.tags or {})
        assert tags.get(TraceTagKey.SPANS_LOCATION) == location, (
            f"Trace {payload['trace_id']} spans location is "
            f"{tags.get(TraceTagKey.SPANS_LOCATION)!r}, expected {location}: {tags}"
        )


def _log_trace_diagnostics(label: str, observed_traces, expected_payloads: list[dict]) -> None:
    now_millis = int(time.time() * 1000)
    for payload in expected_payloads:
        trace = observed_traces.get(payload["trace_id"])
        if trace is None:
            logger.info("%s trace %s is not visible yet", label, payload["trace_id"])
            continue

        span_names = [span.name for span in trace.data.spans]
        logger.info(
            "%s trace %s state=%s experiment_id=%s request_time=%s age_ms=%s "
            "span_count=%d span_names=%s tags=%s trace_metadata=%s",
            label,
            trace.info.trace_id,
            trace.info.state,
            trace.info.experiment_id,
            trace.info.request_time,
            now_millis - trace.info.request_time,
            len(trace.data.spans),
            span_names,
            trace.info.tags,
            trace.info.trace_metadata,
        )


def validate_archival_smoke_ready(test_context: TestContext) -> None:
    """Validate that archival smoke harness state was captured."""
    validate_no_error(test_context)
    state = test_context.archival_state
    assert state.get("namespace"), "Archival smoke namespace was not set"
    assert state.get("job_name"), "Archival smoke job name was not set"
    assert state.get("s3_client") is not None, "Archival smoke S3 client was not created"
    assert state.get("bucket"), "Archival smoke S3 bucket was not set"
    assert "before_archive_count" in state, "Pre-job archive object count was not captured"
    assert state.get("retention_seconds", 0) > 0, "Archival retention seconds were not parsed"


def validate_archival_experiment_created(test_context: TestContext) -> None:
    """Validate that the archival smoke experiment was created."""
    validate_no_error(test_context)
    assert test_context.active_experiment_id, "Archival smoke experiment was not created"
    assert test_context.active_experiment_id in test_context.experiments_to_delete, (
        "Archival smoke experiment was not recorded for cleanup"
    )


def validate_archival_traces_visible(test_context: TestContext) -> None:
    """Validate that seeded archival traces are visible with expected payloads."""
    validate_no_error(test_context)
    state = test_context.archival_state
    expected_payloads = state.get("expected_payloads") or []
    assert expected_payloads, "Archival smoke traces were not seeded"
    _assert_trace_payloads(state.get("observed_traces") or {}, expected_payloads)
    _log_trace_diagnostics("Pre-OTLP", state.get("observed_traces") or {}, expected_payloads)


def validate_archival_traces_db_backed(test_context: TestContext) -> None:
    """Validate that seeded traces now store spans in the tracking store."""
    validate_no_error(test_context)
    state = test_context.archival_state
    expected_payloads = state.get("expected_payloads") or []
    observed = state.get("observed_traces") or {}
    assert expected_payloads, "Archival smoke traces were not seeded"
    _assert_trace_payloads(observed, expected_payloads)
    _assert_spans_location(observed, expected_payloads, SpansLocation.TRACKING_STORE.value)
    _log_trace_diagnostics("Pre-archival", observed, expected_payloads)


def validate_archival_job_completed(test_context: TestContext) -> None:
    """Validate that the manually created archival Job completed."""
    validate_no_error(test_context)
    assert test_context.archival_state.get("job_name") in test_context.jobs_to_delete, (
        "Archival Job was not recorded for cleanup after creation"
    )


def validate_archive_objects_written(test_context: TestContext) -> None:
    """Validate that archival wrote new objects under the configured prefix."""
    validate_no_error(test_context)
    state = test_context.archival_state
    before_count = state.get("before_archive_count", 0)
    after_count = state.get("after_archive_count", 0)
    assert after_count > before_count, (
        "No new archive objects appeared under "
        f"s3://{state.get('bucket')}/{ARCHIVAL_PREFIX} "
        f"(before={before_count}, after={after_count})"
    )


def validate_archival_traces_readable(test_context: TestContext) -> None:
    """Validate that archived traces stay readable from ARCHIVE_REPO."""
    validate_no_error(test_context)
    state = test_context.archival_state
    expected_payloads = state.get("expected_payloads") or []
    observed = state.get("observed_traces") or {}
    assert expected_payloads, "Archival smoke traces were not seeded"
    _assert_trace_payloads(observed, expected_payloads)
    _assert_spans_location(observed, expected_payloads, SpansLocation.ARCHIVE_REPO.value)
    _log_trace_diagnostics("Post-archival", observed, expected_payloads)
