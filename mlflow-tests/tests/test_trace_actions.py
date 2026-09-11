import errno
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.entities.trace_status import TraceStatus
from mlflow.exceptions import MlflowException
from requests import exceptions as requests_exceptions
from urllib3.exceptions import MaxRetryError, NewConnectionError

from .actions import trace_actions
from .actions.trace_actions import action_log_trace, action_post_trace_v3_direct
from .actions.trace_archival_actions import wait_for_expected_traces
from .shared import TestContext
from .validations.trace_archival_validations import _assert_trace_payloads


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def _connection_error_from_socket_error(socket_error: OSError) -> requests_exceptions.ConnectionError:
    connection_error = NewConnectionError(None, str(socket_error))
    connection_error.__cause__ = socket_error
    return requests_exceptions.ConnectionError(
        MaxRetryError(None, "/mlflow/api/3.0/mlflow/traces", reason=connection_error)
    )


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


def test_action_post_trace_v3_direct_retries_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=200)
    post = Mock(
        side_effect=[
            _connection_error_from_socket_error(
                ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
            ),
            response,
        ]
    )
    sleep = Mock()
    monkeypatch.setattr(trace_actions.requests, "post", post)
    monkeypatch.setattr(trace_actions.time, "sleep", sleep)
    monkeypatch.setattr(trace_actions, "get_mlflow_base_uri", lambda: "https://mlflow.example")

    test_context = TestContext(
        active_experiment_id="123",
        active_user=SimpleNamespace(upass="token"),
        active_workspace="workspace",
    )

    action_post_trace_v3_direct(test_context)

    assert post.call_count == 2
    sleep.assert_called_once_with(1)
    assert test_context.current_trace_id is not None
    assert test_context.current_trace_name is not None


def test_action_post_trace_v3_direct_does_not_retry_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=403, text="Permission denied")
    response.json.return_value = {"error": {"code": "PERMISSION_DENIED", "message": "Permission denied"}}
    post = Mock(return_value=response)
    sleep = Mock()
    monkeypatch.setattr(trace_actions.requests, "post", post)
    monkeypatch.setattr(trace_actions.time, "sleep", sleep)
    monkeypatch.setattr(trace_actions, "get_mlflow_base_uri", lambda: "https://mlflow.example")

    test_context = TestContext(
        active_experiment_id="123",
        active_user=SimpleNamespace(upass="token"),
        active_workspace="workspace",
    )

    with pytest.raises(MlflowException, match="Permission denied"):
        action_post_trace_v3_direct(test_context)

    post.assert_called_once()
    sleep.assert_not_called()


def test_action_post_trace_v3_direct_stops_after_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    post = Mock(
        side_effect=_connection_error_from_socket_error(
            ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
        )
    )
    sleep = Mock()
    monkeypatch.setattr(trace_actions.requests, "post", post)
    monkeypatch.setattr(trace_actions.time, "sleep", sleep)
    monkeypatch.setattr(trace_actions, "get_mlflow_base_uri", lambda: "https://mlflow.example")

    test_context = TestContext(
        active_experiment_id="123",
        active_user=SimpleNamespace(upass="token"),
        active_workspace="workspace",
    )

    with pytest.raises(requests_exceptions.ConnectionError, match="refused"):
        action_post_trace_v3_direct(test_context)

    assert post.call_count == 3
    assert sleep.call_args_list == [call(1), call(2)]


def test_action_post_trace_v3_direct_does_not_retry_non_refused_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = Mock(
        side_effect=_connection_error_from_socket_error(
            ConnectionResetError(errno.ECONNRESET, "Connection reset")
        )
    )
    sleep = Mock()
    monkeypatch.setattr(trace_actions.requests, "post", post)
    monkeypatch.setattr(trace_actions.time, "sleep", sleep)
    monkeypatch.setattr(trace_actions, "get_mlflow_base_uri", lambda: "https://mlflow.example")

    test_context = TestContext(
        active_experiment_id="123",
        active_user=SimpleNamespace(upass="token"),
        active_workspace="workspace",
    )

    with pytest.raises(requests_exceptions.ConnectionError):
        action_post_trace_v3_direct(test_context)

    post.assert_called_once()
    sleep.assert_not_called()


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


def test_wait_for_expected_traces_waits_for_root_span(monkeypatch: pytest.MonkeyPatch) -> None:
    child_span = SimpleNamespace(span_id="child-span")
    root_span = SimpleNamespace(span_id="root-span")
    partial_trace = SimpleNamespace(
        info=SimpleNamespace(trace_id="trace-0"), data=SimpleNamespace(spans=[child_span])
    )
    complete_trace = SimpleNamespace(
        info=SimpleNamespace(trace_id="trace-0"),
        data=SimpleNamespace(spans=[child_span, root_span]),
    )
    admin_client = Mock()
    admin_client.search_traces.side_effect = [[partial_trace], [complete_trace]]
    sleep = Mock()
    monotonic = Mock(side_effect=[0, 0, 1])
    monkeypatch.setattr("tests.actions.trace_archival_actions.time.sleep", sleep)
    monkeypatch.setattr("tests.actions.trace_archival_actions.time.monotonic", monotonic)

    observed = wait_for_expected_traces(
        admin_client, "1", {"trace-0": "root-span"}
    )

    assert observed == {"trace-0": complete_trace}
    assert admin_client.search_traces.call_count == 2
    sleep.assert_called_once_with(2)
