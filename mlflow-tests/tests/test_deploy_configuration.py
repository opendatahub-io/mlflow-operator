"""Unit coverage for test-deployment topology decisions."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


_DEPLOY_PATH = (
    Path(__file__).parents[2] / ".github" / "actions" / "deploy" / "deploy.py"
)
_DEPLOY_SPEC = importlib.util.spec_from_file_location(
    "mlflow_operator_deploy", _DEPLOY_PATH
)
assert _DEPLOY_SPEC is not None and _DEPLOY_SPEC.loader is not None
_DEPLOY_MODULE = importlib.util.module_from_spec(_DEPLOY_SPEC)
_DEPLOY_SPEC.loader.exec_module(_DEPLOY_MODULE)


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for deployment-policy tests."""
    return {}


@pytest.mark.parametrize(
    ("artifact_storage", "backend_store", "registry_store", "expected"),
    [
        ("file", "postgres", "postgres", False),
        ("s3", "sqlite", "sqlite", False),
        ("s3", "sqlite", "postgres", False),
        ("s3", "postgres", "sqlite", False),
        ("s3", "postgres", "postgres", True),
        ("externals3", "postgres", "postgres", True),
    ],
)
def test_trace_archival_requires_object_storage_and_remote_metadata(
    artifact_storage: str,
    backend_store: str,
    registry_store: str,
    expected: bool,
) -> None:
    deployer = object.__new__(_DEPLOY_MODULE.MLflowDeployer)
    deployer.args = SimpleNamespace(
        artifact_storage=artifact_storage,
        backend_store=backend_store,
        registry_store=registry_store,
    )

    assert deployer._trace_archival_enabled() is expected


@pytest.mark.parametrize("artifact_storage", ["file", "s3", "externals3"])
def test_artifacts_server_allows_supported_artifact_storage(
    artifact_storage: str,
) -> None:
    deployer = object.__new__(_DEPLOY_MODULE.MLflowDeployer)
    deployer.args = SimpleNamespace(
        artifacts_server=True,
        platform="base",
        backend_store="postgres",
        registry_store="postgres",
        artifact_storage=artifact_storage,
        s3_access_key="test-access-key",
        s3_secret_key="test-secret-key",
        s3_bucket="test-bucket",
        ca_bundle_path="",
        ca_bundle_configmap="",
        skip_infrastructure=True,
        postgres_tls=False,
        seaweedfs_tls=False,
    )

    deployer._validate_args()


def test_generated_file_cr_enables_split_serving_with_persistent_storage() -> None:
    deployer = object.__new__(_DEPLOY_MODULE.MLflowDeployer)
    deployer.args = SimpleNamespace(
        backend_store="postgres",
        registry_store="postgres",
        artifact_storage="file",
        namespace="opendatahub",
        mlflow_image="localhost/mlflow:test",
        backend_store_uri="sqlite:////mlflow/mlflow.db",
        registry_store_uri="sqlite:////mlflow/mlflow.db",
        serve_artifacts="false",
        artifacts_destination="file:///mlflow/artifacts",
        artifacts_server=True,
        workspace_label_selector="",
        ca_bundle_configmap="",
        ca_bundle_path="",
    )
    deployer._tls_ca_bundle_cm = None
    deployer._ca_cert_pem = None
    deployer.run_command = lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, "", ""
    )
    deployer.wait_for_deployment_to_exist = lambda *args, **kwargs: None
    deployer.wait_for_mlflow_ready = lambda *args, **kwargs: None

    deployer.deploy_mlflow()

    spec = yaml.safe_load(Path("/tmp/mlflow-cr.yaml").read_text())["spec"]
    assert spec["backendStoreUriFrom"]["name"] == "mlflow-db-credentials"
    assert spec["registryStoreUriFrom"]["name"] == "mlflow-db-credentials"
    assert spec["artifactsDestination"] == "file:///mlflow/artifacts"
    assert spec["artifactsServer"] == {"enabled": True}
    assert spec["serveArtifacts"] is False
    assert spec["storage"]["accessModes"] == ["ReadWriteOnce"]
    assert "defaultArtifactRoot" not in spec


def test_kind_operator_deployment_applies_mlflow_url_override(tmp_path: Path) -> None:
    deployer = object.__new__(_DEPLOY_MODULE.MLflowDeployer)
    deployer.args = SimpleNamespace(
        namespace="opendatahub",
        mlflow_operator_image="localhost/mlflow-operator:test",
        mlflow_url="https://localhost:8444",
    )
    deployer.repo_root = tmp_path
    updates = []
    deployer.ci_test_infra_path = lambda *parts: tmp_path.joinpath(*parts)
    deployer._set_env_file_value = (
        lambda path, key, value, description=None: updates.append((key, value))
    )
    deployer.generate_tls_certificates = lambda: None
    deployer.run_command = lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, "", ""
    )

    deployer.deploy_mlflow_operator()

    assert ("mlflow-url", "https://localhost:8444") in updates


@pytest.mark.smoke
@pytest.mark.artifacts_server
def test_kind_overlay_rebakes_operator_values_from_overlay_params(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[2]
    test_repo = tmp_path / "repo"
    overlay = test_repo / ".github/test-infra/overlays/kind"
    shutil.copytree(repo_root / "config", test_repo / "config")
    shutil.copytree(repo_root / ".github/test-infra/overlays/kind", overlay)

    overrides = {
        "MLFLOW_IMAGE": "localhost/mlflow:test-runtime",
        "MLFLOW_OPERATOR_IMAGE": "localhost/mlflow-operator:test-manager",
        "gateway-name": "kind-test-gateway",
        "mlflow-url": "https://localhost:8444",
        "section-title": "Kind MLflow Test",
    }
    params = overlay / "params.env"
    entries = [line.split("=", 1) for line in params.read_text().splitlines() if line]
    params.write_text(
        "\n".join(f"{key}={overrides.get(key, value)}" for key, value in entries) + "\n"
    )
    (overlay / "tls.crt").write_text("test certificate")
    (overlay / "tls.key").write_text("test key")

    kustomize = shutil.which("kustomize")
    if kustomize is None:
        local_kustomize = repo_root / "bin/kustomize"
        if not local_kustomize.is_file():
            pytest.skip("kustomize is required to verify the rendered Kind overlay")
        kustomize = str(local_kustomize)

    result = subprocess.run(
        [kustomize, "build", str(overlay)],
        check=True,
        capture_output=True,
        text=True,
    )
    objects = [obj for obj in yaml.safe_load_all(result.stdout) if obj]
    deployment = next(
        obj
        for obj in objects
        if obj.get("kind") == "Deployment"
        and obj["metadata"]["name"] == "mlflow-operator-controller-manager"
    )
    manager = next(
        container
        for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == "manager"
    )
    environment = {entry["name"]: entry.get("value") for entry in manager["env"]}

    assert manager["image"] == overrides["MLFLOW_OPERATOR_IMAGE"]
    assert environment["MLFLOW_IMAGE"] == overrides["MLFLOW_IMAGE"]
    assert environment["GATEWAY_NAME"] == overrides["gateway-name"]
    assert environment["MLFLOW_URL"] == overrides["mlflow-url"]
    assert environment["SECTION_TITLE"] == overrides["section-title"]


@pytest.mark.parametrize(
    ("backend_store", "registry_store", "expected"),
    [
        ("sqlite", "sqlite", False),
        ("sqlite", "postgres", False),
        ("postgres", "sqlite", False),
        ("postgres", "postgres", True),
    ],
)
def test_generated_s3_cr_only_enables_safe_trace_archival(
    backend_store: str,
    registry_store: str,
    expected: bool,
) -> None:
    deployer = object.__new__(_DEPLOY_MODULE.MLflowDeployer)
    deployer.args = SimpleNamespace(
        backend_store=backend_store,
        registry_store=registry_store,
        artifact_storage="s3",
        namespace="opendatahub",
        mlflow_image="localhost/mlflow:test",
        backend_store_uri="sqlite:////mlflow/mlflow.db",
        registry_store_uri="sqlite:////mlflow/mlflow.db",
        serve_artifacts="true",
        s3_bucket="mlpipeline",
        s3_endpoint="http://minio-service:9000",
        trace_archival_retention="1m",
        artifacts_destination="file:///mlflow/artifacts",
        artifacts_server=False,
        workspace_label_selector="",
        ca_bundle_configmap="",
        ca_bundle_path="",
    )
    deployer._tls_ca_bundle_cm = None
    deployer._ca_cert_pem = None
    deployer.run_command = lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, "", ""
    )
    deployer.wait_for_deployment_to_exist = lambda *args, **kwargs: None
    deployer.wait_for_mlflow_ready = lambda *args, **kwargs: None

    deployer.deploy_mlflow()

    mlflow_cr = yaml.safe_load(Path("/tmp/mlflow-cr.yaml").read_text())
    assert ("traceArchival" in mlflow_cr["spec"]) is expected
    assert ("garbageCollection" in mlflow_cr["spec"]) is expected
    if expected:
        assert mlflow_cr["spec"]["garbageCollection"] == {"schedule": "0 0 1 1 *"}
    if not expected:
        assert mlflow_cr["spec"]["storage"]["accessModes"] == ["ReadWriteOnce"]
    else:
        assert "storage" not in mlflow_cr["spec"]
