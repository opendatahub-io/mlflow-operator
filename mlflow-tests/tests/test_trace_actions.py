from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.entities.trace_status import TraceStatus
from mlflow.exceptions import MlflowException

from .actions.trace_actions import action_log_trace
from .validations.trace_archival_validations import _assert_trace_payloads
from .shared import TestContext


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_action_log_trace_raises_permission_denied_for_noop_span() -> None:
    user_client = Mock()
    user_client.start_trace.return_value = SimpleNamespace(trace_id=NO_OP_SPAN_TRACE_ID)

    test_context = TestContext(
        active_experiment_id="123",
        user_client=user_client,
    )

    with pytest.raises(MlflowException, match="Permission denied"):
        action_log_trace(test_context)

    user_client.end_trace.assert_not_called()


def test_action_log_trace_records_trace_id_and_ends_trace() -> None:
    user_client = Mock()
    user_client.start_trace.return_value = SimpleNamespace(request_id="tr-123")

    test_context = TestContext(
        active_experiment_id="123",
        user_client=user_client,
    )

    action_log_trace(test_context)

    assert test_context.current_trace_id == "tr-123"
    assert test_context.current_trace_name is not None
    assert test_context.current_trace_name.startswith("test-trace-")
    user_client.end_trace.assert_called_once_with(
        request_id="tr-123",
        status=TraceStatus.OK,
    )


def test_trace_archival_validation_finds_root_span_without_relying_on_order() -> None:
    root_span = SimpleNamespace(
        span_id="root-span",
        name="trace-archival-smoke-0",
        inputs={"message": "trace archival smoke message 0"},
        outputs={"result": "trace archival smoke result 0"},
    )
    child_span = SimpleNamespace(
        span_id="child-span",
        name="trace-archival-smoke-0-db-backed",
        inputs={},
        outputs={},
    )
    trace = SimpleNamespace(data=SimpleNamespace(spans=[child_span, root_span]))

    _assert_trace_payloads(
        {"trace-0": trace},
        [
            {
                "trace_id": "trace-0",
                "trace_name": root_span.name,
                "message": root_span.inputs["message"],
                "result": root_span.outputs["result"],
                "spans": [root_span, child_span],
            }
        ],
    )
