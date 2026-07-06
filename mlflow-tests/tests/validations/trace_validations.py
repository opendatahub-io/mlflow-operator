"""Validation functions for trace-related operations."""

import logging

import requests
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME

from tests.constants.config import Config
from tests.http_utils import get_mlflow_base_uri, get_requests_verify_value
from tests.shared import TestContext

logger = logging.getLogger(__name__)
def validate_trace_logged(test_context: TestContext) -> None:
    """Validate that a trace was successfully logged."""
    logger.info("Validating trace logging")

    assert test_context.current_trace_id is not None, "Trace ID not set after logging trace"
    assert test_context.current_trace_name is not None, "Trace name not set after logging trace"
    assert test_context.active_user is not None, "Active user not set for trace validation"
    assert test_context.active_workspace is not None, "Active workspace not set for trace validation"

    response = requests.get(
        f"{get_mlflow_base_uri()}/api/3.0/mlflow/traces/{test_context.current_trace_id}",
        headers={
            "Authorization": f"Bearer {test_context.active_user.upass}",
            WORKSPACE_HEADER_NAME: test_context.active_workspace,
        },
        verify=get_requests_verify_value(),
        timeout=Config.REQUEST_TIMEOUT,
    )
    assert response.status_code == 200, (
        f"Trace metadata request failed with status {response.status_code}: {response.text}"
    )
    response_payload = response.json()
    trace_container = response_payload.get("trace")
    assert isinstance(trace_container, dict), (
        f"Trace metadata response missing 'trace' object: {response_payload}"
    )
    trace_payload = trace_container.get("trace_info")
    assert isinstance(trace_payload, dict), (
        f"Trace metadata response missing 'trace_info' object: {response_payload}"
    )
    test_context.active_trace = trace_payload
    assert trace_payload["trace_id"] == test_context.current_trace_id, (
        "Retrieved trace ID does not match the created trace ID"
    )
    assert (
        trace_payload["trace_location"]["mlflow_experiment"]["experiment_id"]
        == test_context.active_experiment_id
    ), (
        "Retrieved trace experiment does not match the active experiment"
    )

    logger.info("Successfully validated trace logging")
