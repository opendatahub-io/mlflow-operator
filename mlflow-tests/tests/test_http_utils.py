import os
from unittest.mock import Mock, patch, sentinel

import pytest

from tests.constants.config import Config
from tests.http_utils import configure_ca_bundle_environment, get_s3_verify_value


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    return {}


def test_configure_ca_bundle_environment(monkeypatch):
    ca_bundle = "/tmp/test-ca-bundle.crt"
    monkeypatch.setattr(Config, "CA_BUNDLE", ca_bundle)
    bundle_variables = (
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "AWS_CA_BUNDLE",
    )
    for name in bundle_variables:
        monkeypatch.setenv(name, os.environ.get(name, ""))

    configure_ca_bundle_environment()

    for name in bundle_variables:
        assert os.environ[name] == ca_bundle


def test_configure_ca_bundle_environment_unsets_empty_bundles(monkeypatch):
    monkeypatch.setattr(Config, "CA_BUNDLE", "")
    bundle_variables = (
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "AWS_CA_BUNDLE",
    )
    for name in bundle_variables:
        monkeypatch.setenv(name, "")

    configure_ca_bundle_environment()

    for name in bundle_variables:
        assert name not in os.environ


def test_configure_ca_bundle_environment_preserves_existing_bundles(monkeypatch):
    monkeypatch.setattr(Config, "CA_BUNDLE", "")
    bundle_variables = (
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "AWS_CA_BUNDLE",
    )
    for name in bundle_variables:
        monkeypatch.setenv(name, f"/tmp/{name.lower()}.crt")

    configure_ca_bundle_environment()

    for name in bundle_variables:
        assert os.environ[name] == f"/tmp/{name.lower()}.crt"


@pytest.mark.parametrize(
    ("endpoint_url", "ca_bundle", "disable_tls", "expected"),
    [
        (
            "https://s3.example.com",
            "/tmp/test-ca-bundle.crt",
            "true",
            "/tmp/test-ca-bundle.crt",
        ),
        ("https://localhost:9000", "/tmp/test-ca-bundle.crt", "false", False),
        (
            "https://s3.example.com",
            "/tmp/test-ca-bundle.crt",
            "false",
            "/tmp/test-ca-bundle.crt",
        ),
        ("https://s3.example.com", "", "true", False),
        ("https://s3.example.com", "", "false", True),
    ],
)
def test_get_s3_verify_value(
    monkeypatch, endpoint_url, ca_bundle, disable_tls, expected
):
    monkeypatch.setattr(Config, "CA_BUNDLE", ca_bundle)
    monkeypatch.setattr(Config, "DISABLE_TLS", disable_tls)

    assert get_s3_verify_value(endpoint_url) == expected


def test_mlflow_client_honors_s3_ignore_tls_with_verify_parameter(monkeypatch):
    from tests import http_utils

    def download_chunk_with_verify(
        *, range_start, range_end, headers, download_path, http_uri, verify=None
    ):
        # Mirrors the mlflow >= 3.17 signature.
        ...

    def download_chunk_without_verify(
        *, range_start, range_end, headers, download_path, http_uri
    ):
        # Mirrors the mlflow <= 3.16 signature.
        ...

    monkeypatch.setattr(
        "mlflow.utils.request_utils.download_chunk", download_chunk_with_verify
    )
    assert http_utils.mlflow_client_honors_s3_ignore_tls() is True

    monkeypatch.setattr(
        "mlflow.utils.request_utils.download_chunk", download_chunk_without_verify
    )
    assert http_utils.mlflow_client_honors_s3_ignore_tls() is False


@pytest.mark.parametrize(
    (
        "storage", "endpoint", "serve_artifacts", "artifacts_server",
        "ca_bundle", "disable_tls", "client_env", "client_supports_verify",
        "should_fail",
    ),
    [
        pytest.param(
            "file", "https://s3.example.com", True, False, "", "true", {}, False, False,
            id="file-with-unused-s3-endpoint",
        ),
        pytest.param(
            "s3", "http://localhost:9000", True, False, "", "true", {}, False, False,
            id="http-s3",
        ),
        pytest.param(
            "s3", "https://s3.example.com", False, False, "", "true", {}, False, False,
            id="direct-s3",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "false", {}, False, False,
            id="https-verification-enabled",
        ),
        pytest.param(
            "s3", "https://localhost:9000", True, False,
            "/tmp/test-ca-bundle.crt", "true", {}, False, False,
            id="seaweedfs-port-forward-with-trusted-presigned-urls",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "true",
            {"REQUESTS_CA_BUNDLE": "/tmp/existing-ca.pem"}, False, False,
            id="https-existing-requests-ca-bundle",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "true",
            {"REQUESTS_CA_BUNDLE": "", "CURL_CA_BUNDLE": "/tmp/existing-ca.pem"}, False, False,
            id="https-existing-curl-ca-bundle",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "true",
            {"MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "False"}, False, False,
            id="https-multipart-download-disabled",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "true",
            {"MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "true"}, False, True,
            id="https-presigned-old-client",
        ),
        pytest.param(
            "s3", "https://s3.example.com", False, True, "", "true", {}, False, True,
            id="https-split-old-client",
        ),
        pytest.param(
            "s3", "https://s3.example.com", True, False, "", "true", {}, True, False,
            id="https-presigned-capable-client",
        ),
    ],
)
def test_setup_clients_checks_tls_for_presigned_https_downloads(
    monkeypatch, storage, endpoint, serve_artifacts, artifacts_server,
    ca_bundle, disable_tls, client_env, client_supports_verify, should_fail,
):
    from tests import conftest, http_utils

    for name, value in {
        "ARTIFACT_STORAGE": storage,
        "S3_URL": endpoint,
        "SERVE_ARTIFACTS": serve_artifacts,
        "ARTIFACTS_SERVER": artifacts_server,
        "CA_BUNDLE": ca_bundle,
        "DISABLE_TLS": disable_tls,
        "AWS_ACCESS_KEY": "test-access-key",
        "AWS_SECRET_KEY": "test-secret-key",
    }.items():
        monkeypatch.setattr(Config, name, value)
    monkeypatch.setattr(
        http_utils, "mlflow_client_honors_s3_ignore_tls", lambda: client_supports_verify
    )
    monkeypatch.setattr(conftest, "_get_configured_workspaces", lambda: ["test-workspace"])
    monkeypatch.setattr(conftest.urllib3, "disable_warnings", Mock())
    monkeypatch.setattr(conftest, "K8Manager", Mock())
    monkeypatch.setattr(conftest, "K8UserManager", Mock())
    create_k8s_client = Mock(return_value=(sentinel.core_api, sentinel.rbac_api))
    create_mlflow_client = Mock(return_value=sentinel.mlflow_client)
    monkeypatch.setattr(conftest.ClientManager, "create_k8s_client", create_k8s_client)
    monkeypatch.setattr(conftest.ClientManager, "create_mlflow_client", create_mlflow_client)

    with patch.dict(os.environ):
        for name in (
            "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        ):
            os.environ.pop(name, None)
        os.environ.update(client_env)
        if should_fail:
            with pytest.raises(RuntimeError, match="MLFLOW_S3_IGNORE_TLS"):
                conftest.setup_clients.__wrapped__()
            create_k8s_client.assert_not_called()
            create_mlflow_client.assert_not_called()
        else:
            admin_client, _, _, workspaces = conftest.setup_clients.__wrapped__()
            assert admin_client is sentinel.mlflow_client
            assert workspaces == ["test-workspace"]
            create_k8s_client.assert_called_once_with()
            if ca_bundle:
                assert os.environ["REQUESTS_CA_BUNDLE"] == ca_bundle
                assert os.environ["AWS_CA_BUNDLE"] == ca_bundle
            for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
                if client_env.get(name):
                    assert os.environ[name] == client_env[name]
            if storage == "s3" and not serve_artifacts and not artifacts_server:
                assert os.environ["MLFLOW_S3_IGNORE_TLS"] == "true"
                assert os.environ["MLFLOW_S3_ENDPOINT_URL"] == endpoint
