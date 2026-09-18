import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from xml.etree.ElementTree import parse

import pytest

from test_write_harness_junit import (
    _mlflow_delete_commands,
    _write_executable,
    bash_with_mapfile,
)


@pytest.fixture
def artifact_settings_harness(
    tmp_path: Path,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    bash = bash_with_mapfile()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "kubectl",
        dedent(
            """\
            #!/bin/sh
            echo "$*" >> "$KUBECTL_LOG"
            case "$*" in
                *".spec.artifactsServer.enabled"*)
                    if [ "$CR_READ_EXIT" != "0" ]; then
                        echo 'Error from server (Forbidden): cannot get MLflow' >&2
                        exit "$CR_READ_EXIT"
                    fi
                    printf '%s' "$CR_ARTIFACT_SETTINGS"
                    ;;
                *"jsonpath={.status.artifactsUrl}"*)
                    printf 'https://mlflow.example/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts'
                    ;;
                *"jsonpath={.status.url}"*) printf 'https://mlflow.example/mlflow' ;;
                *"get httproute mlflow-artifacts"*) printf 'Accepted=True\\nResolvedRefs=True\\n' ;;
                *"create token"*) printf 'fake-token' ;;
            esac
            exit 0
            """
        ),
    )
    _write_executable(
        fake_bin / "curl",
        dedent(
            """\
            #!/bin/sh
            while [ "$#" -gt 0 ]; do
                if [ "$1" = -o ]; then
                    printf '{}' > "$2"
                    shift
                fi
                shift
            done
            printf '200'
            """
        ),
    )
    _write_executable(fake_bin / "sleep", "#!/bin/sh\n/bin/sleep 0.05\n")
    _write_executable(
        fake_bin / "uv",
        dedent(
            """\
            #!/bin/sh
            echo "$*" >> "$UV_LOG"
            case "$*" in
                *pytest*)
                    printf '%s\\n' "artifacts_server=$artifacts_server" \\
                        "serve_artifacts=$serve_artifacts" \\
                        "artifacts_server_gateway=$artifacts_server_gateway" \\
                        "MLFLOW_ARTIFACTS_URI=${MLFLOW_ARTIFACTS_URI-unset}" \\
                        "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" > "$PYTEST_ENV_LOG"
                    ;;
            esac
            exit 0
            """
        ),
    )
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "KUBECTL_LOG": str(tmp_path / "kubectl.log"),
            "UV_LOG": str(tmp_path / "uv.log"),
            "PYTEST_ENV_LOG": str(tmp_path / "pytest.env"),
            "TEST_RESULTS_DIR": str(tmp_path / "results"),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "CR_READ_EXIT": "0",
            "CR_ARTIFACT_SETTINGS": "false|true",
            "ARTIFACTS_SERVER": "true",
            "ARTIFACTS_SERVER_GATEWAY": "true",
            "SERVE_ARTIFACTS": "false",
            "INFRASTRUCTURE_PLATFORM": "openshift",
            "FORCE_PORT_FORWARD": "false",
            "DEPLOY_MLFLOW_OPERATOR": "false",
            "SKIP_DEPLOYMENT": "true",
            "SKIP_OPERATOR": "true",
            "SKIP_INFRASTRUCTURE": "true",
            "SKIP_CLEANUP": "false",
            "CLEANUP_REUSED_RESOURCES": "false",
            "BACKEND_STORE": "sqlite",
            "REGISTRY_STORE": "sqlite",
            "ARTIFACT_BACKENDS": "file",
            "STORAGE_TYPE": "file",
            "SEAWEEDFS_TLS": "false",
            "NAMESPACE": "test-namespace",
            "workspaces": "test-workspace",
            "MLFLOW_ARTIFACTS_URI": "https://stale.example/mlflow-artifacts",
        }
    )

    def run(
        overrides: dict[str, str], args: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [bash, Path(__file__).with_name("test-run.sh"), *args],
            env=env | overrides,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    return run


def read_exports(tmp_path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (tmp_path / "pytest.env").read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.parametrize(
    (
        "settings",
        "overrides",
        "expected_server",
        "expected_serving",
        "expected_gateway",
    ),
    [
        ("false|true", {}, "false", "true", "false"),
        ("false|false", {"SERVE_ARTIFACTS": "true"}, "false", "false", "false"),
        ("|true", {}, "false", "true", "false"),
        ("|", {}, "false", "false", "false"),
        ("false|", {}, "false", "false", "false"),
        (
            "true|false",
            {"ARTIFACTS_SERVER": "false", "SERVE_ARTIFACTS": "true"},
            "true",
            "false",
            "true",
        ),
        ("true|", {"ARTIFACTS_SERVER_GATEWAY": "false"}, "true", "false", "false"),
        (
            "false|true",
            {"INFRASTRUCTURE_PLATFORM": "base", "FORCE_PORT_FORWARD": "true"},
            "false",
            "true",
            "false",
        ),
        (
            "true|false",
            {
                "INFRASTRUCTURE_PLATFORM": "base",
                "ARTIFACTS_SERVER_GATEWAY": "false",
                "ARTIFACT_BACKENDS": "s3",
            },
            "true",
            "false",
            "false",
        ),
    ],
    ids=[
        "tracking",
        "direct-storage",
        "legacy",
        "omitted",
        "omitted-serving",
        "split-gateway",
        "split-direct",
        "disabled-gateway",
        "split-kind-s3",
    ],
)
def test_reused_artifact_settings_override_flags(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    settings: str,
    overrides: dict[str, str],
    expected_server: str,
    expected_serving: str,
    expected_gateway: str,
) -> None:
    result = artifact_settings_harness({"CR_ARTIFACT_SETTINGS": settings, **overrides})
    assert result.returncode == 0, result.stdout + result.stderr
    exported = read_exports(tmp_path)
    assert exported["artifacts_server"] == expected_server
    assert exported["serve_artifacts"] == expected_serving
    assert exported["artifacts_server_gateway"] == expected_gateway
    log = (tmp_path / "kubectl.log").read_text(encoding="utf-8")
    assert log.count(".spec.artifactsServer.enabled") == 1
    assert ".spec.serveArtifacts" in log.splitlines()[0]
    assert ("wait --for=condition=Available deployment/mlflow-artifacts" in log) == (
        expected_server == "true"
    )
    assert ("get httproute mlflow-artifacts" in log) == (expected_gateway == "true")
    assert (".status.artifactsUrl" in log) == (expected_gateway == "true")
    assert ("port-forward svc/mlflow-artifacts" in log) == (
        expected_server == "true" and expected_gateway == "false"
    )
    if expected_server == "false":
        assert exported["MLFLOW_ARTIFACTS_URI"] == "unset"
    elif expected_gateway == "true":
        assert (
            exported["MLFLOW_ARTIFACTS_URI"]
            == "https://mlflow.example/mlflow-artifacts"
        )
    elif overrides.get("ARTIFACT_BACKENDS") == "s3":
        assert (
            exported["MLFLOW_ARTIFACTS_URI"]
            == "https://mlflow-artifacts.test-namespace.svc:8443/mlflow-artifacts"
        )
        assert exported["MLFLOW_TRACKING_URI"] == "https://localhost:8442/mlflow"
    else:
        assert (
            exported["MLFLOW_ARTIFACTS_URI"]
            == "https://localhost:8444/mlflow-artifacts"
        )
    assert "deploy.py" not in (tmp_path / "uv.log").read_text(encoding="utf-8")
    assert _mlflow_delete_commands(log) == []


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            {"CR_READ_EXIT": "1"},
            "Failed to read artifact-serving settings from MLflow CR mlflow",
        ),
        (
            {"CR_ARTIFACT_SETTINGS": "invalid|true"},
            "Invalid artifact-serving settings in MLflow CR mlflow",
        ),
    ],
    ids=["read-failure", "invalid-settings"],
)
def test_reused_artifact_settings_failure_writes_junit(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    overrides: dict[str, str],
    expected_message: str,
) -> None:
    result = artifact_settings_harness(overrides)
    assert result.returncode == 1
    assert expected_message in result.stderr
    case = (
        parse(tmp_path / "results" / "xunit_report_file.xml")
        .getroot()
        .find("./testsuite/testcase")
    )
    assert case is not None
    assert case.get("name") == "test_read_artifact_settings"
    error = case.find("error")
    assert error is not None
    assert error.get("message") == expected_message
    assert not (tmp_path / "uv.log").exists()
    log = (tmp_path / "kubectl.log").read_text(encoding="utf-8")
    assert "wait " not in log
    assert "port-forward " not in log
    assert _mlflow_delete_commands(log) == []


@pytest.mark.parametrize(
    ("server", "serving", "expected_serving"),
    [("true", "true", "false"), ("false", "true", "true"), ("false", "false", "false")],
)
def test_fresh_artifact_settings_use_flags(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    server: str,
    serving: str,
    expected_serving: str,
) -> None:
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "CR_READ_EXIT": "1",
            "ARTIFACTS_SERVER": server,
            "SERVE_ARTIFACTS": serving,
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
        }
    )
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "kubectl.log").read_text(encoding="utf-8")
    assert ".spec.artifactsServer.enabled" not in log
    deploy = next(
        line
        for line in (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()
        if "deploy.py" in line
    )
    assert f"--serve-artifacts {expected_serving}" in deploy
    assert ("--artifacts-server" in deploy) == (server == "true")
    exported = read_exports(tmp_path)
    assert exported["artifacts_server"] == server
    assert exported["serve_artifacts"] == expected_serving


@pytest.mark.parametrize(
    "overrides", [{"FORCE_PORT_FORWARD": "true"}, {"INFRASTRUCTURE_PLATFORM": "base"}]
)
def test_reused_split_server_preserves_gateway_validation(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    overrides: dict[str, str],
) -> None:
    result = artifact_settings_harness(
        {"CR_ARTIFACT_SETTINGS": "true|false", **overrides}
    )
    assert result.returncode == 1
    assert "ARTIFACTS_SERVER_GATEWAY=true" in result.stderr
    assert not (tmp_path / "uv.log").exists()


@pytest.mark.parametrize(
    ("phase", "version", "expected_uri"),
    [
        ("pre_upgrade", "3.10", "https://mlflow.example"),
        ("post_upgrade", "3.14", "https://mlflow.example/mlflow"),
    ],
)
def test_reused_upgrade_keeps_tracking_uri_shape(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    phase: str,
    version: str,
    expected_uri: str,
) -> None:
    result = artifact_settings_harness(
        {"MLFLOW_TEST_SUPPORTED_VERSION": version}, ("-m", phase)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    exported = read_exports(tmp_path)
    assert exported["MLFLOW_TRACKING_URI"] == expected_uri
    assert exported["artifacts_server"] == "false"
    assert exported["serve_artifacts"] == "true"
    assert exported["artifacts_server_gateway"] == "false"
