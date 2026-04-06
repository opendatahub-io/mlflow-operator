"""Trace action functions."""

import json
import logging
import time
import uuid

import requests
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.entities.trace import Trace
from mlflow.entities.trace_data import TraceData
from mlflow.entities.trace_info import TraceInfo
from mlflow.entities.trace_location import TraceLocation
from mlflow.entities.trace_state import TraceState
from mlflow.entities.trace_status import TraceStatus
from mlflow.exceptions import MlflowException
from mlflow.protos import databricks_pb2
from mlflow.protos.service_pb2 import StartTraceV3
from mlflow.utils.proto_json_utils import message_to_json
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME

from tests.constants.config import Config
from tests.http_utils import get_mlflow_base_uri, get_requests_verify_value
from tests.shared import TestContext

logger = logging.getLogger(__name__)
def action_log_trace(test_context: TestContext) -> None:
    """Log a trace via the SDK-oriented client path.

    The integration suite validates RBAC through the direct v3 REST endpoint instead of
    this helper because `MlflowClient.start_trace()` can collapse backend failures into a
    no-op span. Keep this helper for unit coverage of that client behavior.
    """
    if not test_context.active_experiment_id:
        raise ValueError("test_context.active_experiment_id must be set before logging traces")
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before logging traces")

    trace_name = f"test-trace-{uuid.uuid4().hex[:8]}"
    logger.info(
        "Logging trace '%s' to experiment %s",
        trace_name,
        test_context.active_experiment_id,
    )

    span = test_context.user_client.start_trace(
        name=trace_name,
        experiment_id=test_context.active_experiment_id,
    )
    trace_id = getattr(span, "request_id", None) or getattr(span, "trace_id", None)
    if trace_id is None:
        raise ValueError("start_trace did not return a request_id or trace_id")
    if trace_id == NO_OP_SPAN_TRACE_ID:
        # MLflow converts backend trace-start failures into a no-op span instead of surfacing the
        # original server exception. Treat that result as a denied trace write so RBAC tests can
        # validate the authorization outcome rather than silently passing a no-op trace through.
        raise MlflowException(
            "Permission denied for requested trace operation; MLflow returned a no-op span.",
            error_code=databricks_pb2.PERMISSION_DENIED,
        )

    test_context.current_trace_id = trace_id
    test_context.current_trace_name = trace_name

    test_context.user_client.end_trace(
        request_id=trace_id,
        status=TraceStatus.OK,
    )
    logger.info("Successfully logged trace '%s' with ID %s", trace_name, trace_id)


def action_post_trace_v3_direct(test_context: TestContext) -> None:
    """Post a trace directly to the v3 REST endpoint."""
    if not test_context.active_experiment_id:
        raise ValueError("test_context.active_experiment_id must be set before posting traces")
    if not test_context.active_user or not test_context.active_user.upass:
        raise ValueError("test_context.active_user with a bearer token must be set before posting traces")
    if not test_context.active_workspace:
        raise ValueError("test_context.active_workspace must be set before posting traces")

    trace_name = f"test-trace-{uuid.uuid4().hex[:8]}"
    trace_id = f"tr-{uuid.uuid4().hex}"
    logger.info(
        "Posting direct v3 trace '%s' to experiment %s",
        trace_name,
        test_context.active_experiment_id,
    )

    trace_info = TraceInfo(
        trace_id=trace_id,
        trace_location=TraceLocation.from_experiment_id(test_context.active_experiment_id),
        request_time=int(time.time() * 1000),
        state=TraceState.OK,
    )
    trace = Trace(info=trace_info, data=TraceData(spans=[]))
    response = requests.post(
        f"{get_mlflow_base_uri()}/api/3.0/mlflow/traces",
        json=json.loads(message_to_json(StartTraceV3(trace=trace.to_proto()))),
        headers={
            "Authorization": f"Bearer {test_context.active_user.upass}",
            WORKSPACE_HEADER_NAME: test_context.active_workspace,
        },
        verify=get_requests_verify_value(),
        timeout=Config.REQUEST_TIMEOUT,
    )

    if response.status_code >= 400:
        response_message = response.text
        error_code = databricks_pb2.INTERNAL_ERROR
        try:
            response_payload = response.json()
            error_payload = response_payload.get("error", {})
            response_message = error_payload.get(
                "message",
                response_payload.get("message", response_message),
            )
            if (
                error_payload.get("code") == "PERMISSION_DENIED"
                or response_payload.get("error_code") == "PERMISSION_DENIED"
            ):
                error_code = databricks_pb2.PERMISSION_DENIED
        except ValueError:
            pass
        raise MlflowException(response_message, error_code=error_code)

    test_context.current_trace_id = trace_id
    test_context.current_trace_name = trace_name
    logger.info("Successfully posted direct v3 trace '%s' with ID %s", trace_name, trace_id)
