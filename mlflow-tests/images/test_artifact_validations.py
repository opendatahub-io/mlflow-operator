import subprocess
import sys
from pathlib import Path


def test_validate_storage_supports_all_artifact_access_modes() -> None:
    project_dir = Path(__file__).parents[1]
    script = """
from tests.constants.config import Config
from tests.shared import TestContext
from tests.validations.artifact_validations import validate_storage

artifact_server_uri = "https://mlflow.example/mlflow-artifacts"
artifact_server_location = (
    f"{artifact_server_uri}/api/2.0/mlflow-artifacts/artifacts/"
    "workspaces/workspace/1/run/artifacts"
)
cases = [
    ("file", False, True, artifact_server_uri, artifact_server_location),
    ("s3", False, True, artifact_server_uri, artifact_server_location),
    ("s3", False, False, "", "s3://bucket/artifacts/1/run/artifacts"),
    ("file", True, False, "", "mlflow-artifacts:/workspaces/workspace/1/run/artifacts"),
]

for artifact_storage, serve_artifacts, artifacts_server, artifacts_uri, location in cases:
    Config.ARTIFACT_STORAGE = artifact_storage
    Config.SERVE_ARTIFACTS = serve_artifacts
    Config.ARTIFACTS_SERVER = artifacts_server
    Config.MLFLOW_ARTIFACTS_URI = artifacts_uri
    validate_storage(TestContext(artifact_location=location))

Config.ARTIFACTS_SERVER = True
Config.MLFLOW_ARTIFACTS_URI = artifact_server_uri
try:
    validate_storage(
        TestContext(
            artifact_location="https://other.example/mlflow-artifacts/"
            "api/2.0/mlflow-artifacts/artifacts/workspaces/workspace/1/run/artifacts"
        )
    )
except AssertionError as error:
    assert "Expected dedicated artifact server location" in str(error)
else:
    raise AssertionError("A different artifact server route was accepted")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


