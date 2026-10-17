import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


DEPLOY_PY = Path(__file__).parents[2] / ".github/actions/deploy/deploy.py"
SPEC = importlib.util.spec_from_file_location("deploy", DEPLOY_PY)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


@pytest.mark.parametrize("failure_stage", [None, "certificates", "apply", "wait"])
def test_operator_url_override_is_temporary_before_gateway_deployment(
    tmp_path: Path, failure_stage: str | None
) -> None:
    deployer = object.__new__(deploy.MLflowDeployer)
    deployer.args = SimpleNamespace(
        namespace="mlflow-test",
        mlflow_operator_image="operator:fake",
        mlflow_url="https://localhost:8444",
    )
    deployer.repo_root = tmp_path
    deployer.ci_test_infra_path = lambda *parts: tmp_path.joinpath(*parts)
    params = tmp_path / "overlays/kind/params.env"
    params.parent.mkdir(parents=True)
    params.write_text(
        "# Keep the configured Gateway URL\n"
        "NAMESPACE=mlflow-test\nMLFLOW_OPERATOR_IMAGE=operator:fake\n"
        "mlflow-url=https://configured-gateway.example\n"
    )
    original = params.read_bytes()
    rendered_urls = []

    def generate_certificates():
        if failure_stage == "certificates":
            raise RuntimeError("certificate failure")

    def run_command(command, description=None, **kwargs):
        if "kustomize build" in command:
            rendered_urls.append(
                next(
                    line for line in params.read_text().splitlines()
                    if line.startswith("mlflow-url=")
                )
            )
            if failure_stage == "apply":
                raise RuntimeError("apply failure")
        if "kubectl wait" in command and failure_stage == "wait":
            raise RuntimeError("wait failure")
        return subprocess.CompletedProcess(command, 0, "", "")

    deployer.generate_tls_certificates = generate_certificates
    deployer.run_command = run_command
    deployer.debug_deployment = lambda *args: None

    if failure_stage:
        with pytest.raises(RuntimeError, match="failure"):
            deployer.deploy_mlflow_operator()
    else:
        deployer.deploy_mlflow_operator()

    assert params.read_bytes() == original
    if failure_stage != "certificates":
        assert rendered_urls == ["mlflow-url=https://localhost:8444"]

    failure_stage = None
    deployer.args.mlflow_url = ""
    deployer.deploy_mlflow_operator()
    assert rendered_urls[-1] == "mlflow-url=https://configured-gateway.example"
    assert params.read_bytes() == original


def test_create_postgres_secret_applies_an_existing_secret() -> None:
    args = SimpleNamespace(
        artifact_storage="externals3",
        namespace="mlflow-test",
        postgres_host="postgres.example.test",
        postgres_port="5432",
        postgres_user="mlflow",
        postgres_password="password",
        postgres_backend_db="backend",
        postgres_registry_db="registry",
        postgres_sslmode="verify-full",
        s3_endpoint="",
        seaweedfs_tls=False,
    )
    deployer = deploy.MLflowDeployer(args)
    commands = []

    def run_command(command, description=None, **kwargs):
        commands.append((command, description, kwargs))
        manifest = Path(command[-1])
        secret = yaml.safe_load(manifest.read_text())
        assert secret == {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "mlflow-db-credentials", "namespace": "mlflow-test"},
            "type": "Opaque",
            "stringData": {
                "backend-store-uri": "postgresql://mlflow:password@postgres.example.test:5432/backend?sslmode=verify-full",
                "registry-store-uri": "postgresql://mlflow:password@postgres.example.test:5432/registry?sslmode=verify-full",
            },
        }

    deployer.run_command = run_command
    deployer.create_postgres_secret()

    assert len(commands) == 1
    assert commands[0][0][:3] == ["kubectl", "apply", "-f"]
    assert commands[0][1] == "Applying PostgreSQL credentials secret"
