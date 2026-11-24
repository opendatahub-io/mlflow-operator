import base64
import json
import logging
from unittest.mock import Mock

import psycopg2
import pytest
from kubernetes import client
from urllib3 import HTTPResponse

from tests.actions import job_utils, trace_rollups_actions


BACKEND_URI = "postgresql://test-user:synthetic-password@database.invalid/mlflow"
ENCODED_BACKEND_URI = base64.b64encode(BACKEND_URI.encode()).decode()


def _response(body, *, status=200):
    return HTTPResponse(body=json.dumps(body).encode(), status=status)


@pytest.fixture
def kubernetes_api(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="kubernetes.client.rest")
    with client.ApiClient(client.Configuration()) as api_client:
        request = Mock()
        monkeypatch.setattr(api_client.rest_client.pool_manager, "request", request)
        yield api_client, request


def _assert_no_credentials(output):
    assert "synthetic-password" not in output
    assert ENCODED_BACKEND_URI not in output


def test_rollups_secret_is_read_without_debug_logging(kubernetes_api, caplog):
    api_client, request = kubernetes_api
    response = _response({"data": {"backend-store-uri": ENCODED_BACKEND_URI}})
    request.return_value = response

    assert (
        trace_rollups_actions._read_backend_uri(client.CoreV1Api(api_client), "test")
        == BACKEND_URI
    )
    assert response.closed
    _assert_no_credentials(caplog.text)


def test_rollups_secret_http_error_omits_response_body(kubernetes_api, caplog):
    api_client, request = kubernetes_api
    request.return_value = _response(
        {"message": f"{BACKEND_URI} {ENCODED_BACKEND_URI}"}, status=403
    )

    with pytest.raises(RuntimeError, match="Could not read the harness PostgreSQL Secret") as exc:
        trace_rollups_actions._read_backend_uri(client.CoreV1Api(api_client), "test")

    _assert_no_credentials(str(exc.getrepr()) + caplog.text)


@pytest.mark.parametrize(
    ("failing_call", "message"),
    [("parse_dsn", "Could not parse"), ("connect", "Could not connect")],
)
def test_rollups_database_errors_omit_credentials(monkeypatch, failing_call, message):
    target = psycopg2.extensions if failing_call == "parse_dsn" else psycopg2
    monkeypatch.setattr(
        target, failing_call, Mock(side_effect=psycopg2.OperationalError(BACKEND_URI))
    )

    with pytest.raises(RuntimeError, match=message) as exc:
        trace_rollups_actions._database_connection(
            backend_uri=BACKEND_URI, local_port=5432, core_api=Mock(), state={}
        )

    _assert_no_credentials(str(exc.getrepr()))


def test_failed_job_emits_structured_diagnostics(kubernetes_api, monkeypatch, caplog):
    api_client, request = kubernetes_api
    monkeypatch.setattr(job_utils, "JOB_WAIT_POLL_INTERVAL_SECONDS", 0)
    message = f"{BACKEND_URI} {ENCODED_BACKEND_URI}"
    failed_condition = {"type": "Failed", "status": "True", "message": message}
    container_status = {
        "name": "test-container",
        "image": "test-image",
        "imageID": "test-image",
        "ready": False,
        "restartCount": 0,
        "state": {"terminated": {"exitCode": 1, "reason": "Error", "message": message}},
    }
    pod = {
        "metadata": {"name": "test-pod"},
        "status": {"phase": "Failed", "containerStatuses": [container_status]},
    }
    event = {
        "metadata": {"name": "test-event"},
        "involvedObject": {"kind": "Pod", "name": "test-pod"},
        "type": "Warning",
        "reason": "BackOff",
        "message": message,
    }
    request.side_effect = [
        _response({"message": message}, status=404),
        _response({"status": {"conditions": [failed_condition]}}),
        _response({"items": [pod]}),
        _response({"items": [event]}),
    ]

    with pytest.raises(pytest.fail.Exception, match="Job test-job Failed before completion") as exc:
        job_utils.wait_for_job_complete(
            client.BatchV1Api(api_client),
            client.CoreV1Api(api_client),
            "test-job",
            "test",
            job_description="Trace smoke",
        )

    assert "Pod test-pod phase=Failed" in str(exc.value)
    assert "reason=Error exit_code=1" in str(exc.value)
    assert "event type=Warning reason=BackOff" in str(exc.value)
    assert request.call_count == 4
    _assert_no_credentials(str(exc.getrepr()) + caplog.text)
