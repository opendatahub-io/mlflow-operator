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
                    if [ "$CR_STATUS_READ_EXIT" != "0" ]; then
                        echo 'Error from server (Forbidden): cannot get MLflow status' >&2
                        exit "$CR_STATUS_READ_EXIT"
                    fi
                    if [ -f "$OPERATOR_URL_FILE" ]; then
                        printf '%s/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts' "$(cat "$OPERATOR_URL_FILE")"
                    else
                        printf '%s' "$CR_STATUS_ARTIFACTS_URL"
                    fi
                    ;;
                *"jsonpath={.status.url}"*)
                    if [ -f "$OPERATOR_URL_FILE" ]; then
                        operator_url=$(cat "$OPERATOR_URL_FILE")
                        if [ -n "$operator_url" ]; then
                            printf '%s/mlflow' "$operator_url"
                        fi
                    else
                        printf '%s' "$CR_STATUS_URL"
                    fi
                    ;;
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
            echo "$*" >> "$CURL_LOG"
            case "$*" in
                *gateway-hostname-placeholder*) printf '000'; exit 7 ;;
            esac
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
                *deploy.py*)
                    case " $* " in
                        *" --skip-operator "*) ;;
                        *)
                            # Model the operator URL from the actual overlay and
                            # the URL override passed by the harness to deploy.py.
                            operator_url=$(sed -n 's/^mlflow-url=//p' "$OPERATOR_PARAMS_ENV")
                            previous=""
                            for argument in "$@"; do
                                if [ "$previous" = "--mlflow-url" ]; then
                                    operator_url="$argument"
                                fi
                                previous="$argument"
                            done
                            printf '%s' "$operator_url" > "$OPERATOR_URL_FILE"
                            ;;
                    esac
                    ;;
                *pytest*)
                    printf '%s\\n' "artifacts_server=$artifacts_server" \\
                        "serve_artifacts=$serve_artifacts" \\
                        "artifacts_server_gateway=$artifacts_server_gateway" \\
                        "MLFLOW_ARTIFACTS_URI=${MLFLOW_ARTIFACTS_URI-unset}" \\
                        "MLFLOW_ARTIFACTS_ROOT=${MLFLOW_ARTIFACTS_ROOT-unset}" \\
                        "MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI" > "$PYTEST_ENV_LOG"
                    cp "$PYTEST_ENV_LOG" "${PYTEST_ENV_LOG}.${artifact_storage}"
                    ;;
            esac
            exit 0
            """
        ),
    )
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.pop("DEPLOY_MLFLOW_OPERATOR", None)
    env.pop("FORCE_PORT_FORWARD", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "KUBECTL_LOG": str(tmp_path / "kubectl.log"),
            "CURL_LOG": str(tmp_path / "curl.log"),
            "OPERATOR_URL_FILE": str(tmp_path / "operator.url"),
            "OPERATOR_PARAMS_ENV": str(
                Path(__file__).parents[2]
                / ".github/test-infra/overlays/kind/params.env"
            ),
            "UV_LOG": str(tmp_path / "uv.log"),
            "PYTEST_ENV_LOG": str(tmp_path / "pytest.env"),
            "TEST_RESULTS_DIR": str(tmp_path / "results"),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "CR_READ_EXIT": "0",
            "CR_ARTIFACT_SETTINGS": "false|true",
            "CR_STATUS_READ_EXIT": "0",
            "CR_STATUS_URL": "https://mlflow.example/mlflow",
            "CR_STATUS_ARTIFACTS_URL": "https://mlflow.example/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts",
            "ARTIFACTS_SERVER": "true",
            "ARTIFACTS_SERVER_GATEWAY": "true",
            "SERVE_ARTIFACTS": "false",
            "INFRASTRUCTURE_PLATFORM": "openshift",
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
            "MLFLOW_ARTIFACTS_ROOT": "https://stale.example/artifacts",
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
                "CR_STATUS_ARTIFACTS_URL": "",
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
    assert (".status.artifactsUrl" in log) == (expected_server == "true")
    assert ("port-forward svc/mlflow-artifacts" in log) == (
        expected_server == "true" and expected_gateway == "false"
    )
    if expected_server == "false":
        assert exported["MLFLOW_ARTIFACTS_URI"] == "unset"
        assert exported["MLFLOW_ARTIFACTS_ROOT"] == "unset"
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
    status_root = overrides.get(
        "CR_STATUS_ARTIFACTS_URL",
        "https://mlflow.example/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts",
    )
    if expected_server == "true":
        assert exported["MLFLOW_ARTIFACTS_ROOT"] == (
            status_root
            or f"{exported['MLFLOW_ARTIFACTS_URI']}/api/2.0/mlflow-artifacts/artifacts"
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


@pytest.mark.parametrize("gateway", [True, False], ids=["gateway", "direct"])
def test_artifacts_status_read_failure_writes_junit(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    gateway: bool,
) -> None:
    result = artifact_settings_harness(
        {
            "ARTIFACTS_SERVER_GATEWAY": str(gateway).lower(),
            "CR_ARTIFACT_SETTINGS": "true|false",
            "CR_STATUS_READ_EXIT": "1",
        }
    )
    message = "Failed to read status.artifactsUrl from MLflow CR mlflow"
    assert result.returncode == 1
    assert message in result.stderr
    case = (
        parse(tmp_path / "results" / "xunit_report_file.xml")
        .getroot()
        .find("./testsuite/testcase")
    )
    assert case is not None
    assert case.get("name") == "test_read_artifacts_status_url"
    error = case.find("error")
    assert error is not None
    assert error.get("message") == message
    assert not (tmp_path / "uv.log").exists()


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


@pytest.mark.parametrize("legacy_mode", [None, "false"])
@pytest.mark.parametrize("skip_operator", ["false", "true"])
def test_operator_setup_without_csv_injection(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    legacy_mode: str | None,
    skip_operator: str,
) -> None:
    overrides = {
        "SKIP_DEPLOYMENT": "false",
        "SKIP_OPERATOR": skip_operator,
        "ARTIFACTS_SERVER": "false",
        "ARTIFACTS_SERVER_GATEWAY": "false",
    }
    if legacy_mode is not None:
        overrides["DEPLOY_MLFLOW_OPERATOR"] = legacy_mode
    result = artifact_settings_harness(overrides)

    assert result.returncode == 0, result.stdout + result.stderr
    deploy_commands = [
        line
        for line in (tmp_path / "uv.log").read_text().splitlines()
        if "deploy.py" in line
    ]
    assert len(deploy_commands) == 1
    assert ("--skip-operator" in deploy_commands[0]) == (skip_operator == "true")
    kubectl_commands = (tmp_path / "kubectl.log").read_text().splitlines()
    assert not any("csv" in line.split() for line in kubectl_commands)
    assert (tmp_path / "pytest.env").exists()


def test_retired_injection_request_fails_before_deployment(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = artifact_settings_harness(
        {"DEPLOY_MLFLOW_OPERATOR": "true", "SKIP_DEPLOYMENT": "false"}
    )

    assert result.returncode == 1
    assert "manifest injection has been retired" in result.stderr
    assert "SKIP_OPERATOR=true" in result.stderr
    assert not (tmp_path / "uv.log").exists()
    assert not (tmp_path / "pytest.env").exists()
    kubectl_commands = (tmp_path / "kubectl.log").read_text().splitlines()
    assert all(line.startswith("get ") for line in kubectl_commands)
    reports = list((tmp_path / "results").glob("xunit_report*.xml"))
    assert len(reports) == 1
    case = parse(reports[0]).getroot().find("./testsuite/testcase")
    assert case is not None
    assert case.get("name") == "test_config"
    error = case.find("error")
    assert error is not None
    assert "manifest injection has been retired" in error.get("message", "")


@pytest.mark.parametrize(
    "operator_url", [None, ""], ids=["placeholder-gateway", "no-public-route"]
)
def test_standalone_openshift_readiness_uses_port_forward(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    operator_url: str | None,
) -> None:
    overrides = {
        "SKIP_DEPLOYMENT": "false",
        "SKIP_OPERATOR": "false",
        "ARTIFACTS_SERVER": "false",
        "ARTIFACTS_SERVER_GATEWAY": "false",
    }
    if operator_url is not None:
        params = tmp_path / "params.env"
        params.write_text(f"mlflow-url={operator_url}\n")
        overrides["OPERATOR_PARAMS_ENV"] = str(params)
    result = artifact_settings_harness(overrides)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "gateway-hostname-placeholder" in (tmp_path / "operator.url").read_text()
        or operator_url == ""
    )
    assert (
        read_exports(tmp_path)["MLFLOW_TRACKING_URI"] == "https://localhost:8443/mlflow"
    )
    commands = (tmp_path / "kubectl.log").read_text()
    assert "port-forward svc/mlflow -n test-namespace 8443:8443" in commands
    assert "jsonpath={.status.url}" not in commands
    probes = (tmp_path / "curl.log").read_text()
    assert "https://localhost:8443/mlflow/api/3.0/mlflow/server-info" in probes
    assert "gateway-hostname-placeholder" not in probes


@pytest.mark.parametrize("gateway", ["false", "true"])
def test_installed_openshift_operator_keeps_gateway_readiness(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    gateway: str,
) -> None:
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "true",
            "ARTIFACTS_SERVER": gateway,
            "ARTIFACTS_SERVER_GATEWAY": gateway,
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        read_exports(tmp_path)["MLFLOW_TRACKING_URI"] == "https://mlflow.example/mlflow"
    )
    commands = (tmp_path / "kubectl.log").read_text()
    assert "jsonpath={.status.url}" in commands
    assert "port-forward svc/mlflow " not in commands
    assert (
        "https://mlflow.example/mlflow/api/3.0/mlflow/server-info"
        in (tmp_path / "curl.log").read_text()
    )


@pytest.mark.parametrize(
    "backend,tracking_port,artifact_host,artifact_port",
    [
        ("file", 8443, "localhost", 8444),
        ("s3", 8442, "mlflow-artifacts.test-namespace.svc", 8443),
        ("externals3", 8442, "mlflow-artifacts.test-namespace.svc", 8443),
    ],
)
def test_standalone_openshift_split_server_uses_direct_service_urls(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    backend: str,
    tracking_port: int,
    artifact_host: str,
    artifact_port: int,
) -> None:
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "false",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
            "ARTIFACT_BACKENDS": backend,
            "AWS_ACCESS_KEY_ID": "fake-test-key",
            "AWS_SECRET_ACCESS_KEY": "fake-test-secret",
            "BUCKET": "fake-test-bucket",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    exported = read_exports(tmp_path)
    assert (
        exported["MLFLOW_TRACKING_URI"] == f"https://localhost:{tracking_port}/mlflow"
    )
    assert (
        exported["MLFLOW_ARTIFACTS_URI"]
        == f"https://{artifact_host}:{artifact_port}/mlflow-artifacts"
    )
    assert exported["MLFLOW_ARTIFACTS_ROOT"].startswith(
        f"https://{artifact_host}:{artifact_port}/"
    )
    assert (
        tmp_path / "operator.url"
    ).read_text() == f"https://{artifact_host}:{artifact_port}"
    commands = (tmp_path / "kubectl.log").read_text()
    assert f"port-forward svc/mlflow -n test-namespace {tracking_port}:8443" in commands
    assert (
        f"port-forward svc/mlflow-artifacts -n test-namespace {artifact_port}:8443"
        in commands
    )
    assert "gateway-hostname-placeholder" not in (tmp_path / "curl.log").read_text()


@pytest.mark.parametrize(
    "overrides",
    [
        {"FORCE_PORT_FORWARD": "false", "ARTIFACTS_SERVER_GATEWAY": "false"},
        {"ARTIFACTS_SERVER_GATEWAY": "true"},
    ],
    ids=["explicit-gateway-access", "gateway-validation"],
)
def test_standalone_openshift_preserves_requested_gateway_access(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    overrides: dict[str, str],
) -> None:
    params = tmp_path / "params.env"
    params.write_text("mlflow-url=https://configured-gateway.example\n")
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "false",
            "OPERATOR_PARAMS_ENV": str(params),
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
        }
        | overrides
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert read_exports(tmp_path)["MLFLOW_TRACKING_URI"] == (
        "https://configured-gateway.example/mlflow"
    )
    commands = (tmp_path / "kubectl.log").read_text()
    assert "jsonpath={.status.url}" in commands
    assert "port-forward svc/mlflow " not in commands
    assert (
        "https://configured-gateway.example/mlflow/api/3.0/mlflow/server-info"
        in (tmp_path / "curl.log").read_text()
    )


@pytest.mark.parametrize("platform", ["base", "openshift"])
@pytest.mark.parametrize("s3_backend", ["s3", "externals3"])
@pytest.mark.parametrize("s3_first", [False, True], ids=["file-first", "s3-first"])
def test_direct_split_server_refreshes_operator_url_between_suites(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    platform: str,
    s3_backend: str,
    s3_first: bool,
) -> None:
    backends = [s3_backend, "file"] if s3_first else ["file", s3_backend]
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "false",
            "INFRASTRUCTURE_PLATFORM": platform,
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
            "ARTIFACT_BACKENDS": ",".join(backends),
            "AWS_ACCESS_KEY_ID": "fake-test-key",
            "AWS_SECRET_ACCESS_KEY": "fake-test-secret",
            "BUCKET": "fake-test-bucket",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    deployments = [
        line
        for line in (tmp_path / "uv.log").read_text().splitlines()
        if "deploy.py" in line
    ]
    assert len(deployments) == 2
    for backend, deployment in zip(backends, deployments, strict=True):
        assert "--skip-operator" not in deployment
        base_url = (
            "https://localhost:8444"
            if backend == "file"
            else "https://mlflow-artifacts.test-namespace.svc:8443"
        )
        assert f"--mlflow-url {base_url}" in deployment
        suite = "file" if backend == "file" else "s3"
        exports = dict(
            line.split("=", 1)
            for line in (tmp_path / f"pytest.env.{suite}").read_text().splitlines()
        )
        assert exports["MLFLOW_ARTIFACTS_URI"] == f"{base_url}/mlflow-artifacts"
        assert exports["MLFLOW_ARTIFACTS_ROOT"] == (
            f"{base_url}/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts"
        )
        port = 8443 if backend == "file" else 8442
        assert exports["MLFLOW_TRACKING_URI"] == f"https://localhost:{port}/mlflow"


@pytest.mark.parametrize("s3_backend", ["s3", "externals3"])
def test_direct_split_server_never_refreshes_explicitly_reused_operator(
    tmp_path: Path,
    artifact_settings_harness: Callable[..., subprocess.CompletedProcess[str]],
    s3_backend: str,
) -> None:
    result = artifact_settings_harness(
        {
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "true",
            "FORCE_PORT_FORWARD": "true",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
            "ARTIFACT_BACKENDS": f"file,{s3_backend}",
            "AWS_ACCESS_KEY_ID": "fake-test-key",
            "AWS_SECRET_ACCESS_KEY": "fake-test-secret",
            "BUCKET": "fake-test-bucket",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    deployments = [
        line
        for line in (tmp_path / "uv.log").read_text().splitlines()
        if "deploy.py" in line
    ]
    assert len(deployments) == 2
    assert all("--skip-operator" in line for line in deployments)
    assert not (tmp_path / "operator.url").exists()


@pytest.mark.parametrize(
    "backend,gateway,expect_artifact_host,expect_minio_host",
    [
        ("s3", "false", True, True),
        ("externals3", "false", True, False),
        ("externals3", "true", False, False),
        ("file", "false", False, False),
        ("file, externals3", "false", True, False),
        (" file , s3 ", "false", True, True),
        ("\t s3\t, externals3\n", "false", True, True),
        ("file, externals3", "true", False, False),
    ],
)
def test_test_container_maps_direct_artifact_service_host(
    tmp_path: Path,
    backend: str,
    gateway: str,
    expect_artifact_host: bool,
    expect_minio_host: bool,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "kubectl", "#!/bin/sh\nprintf 'True'\n")
    docker_log = tmp_path / "docker.args"
    _write_executable(
        fake_bin / "docker", '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_LOG"\n'
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
        "TEST_RESULTS_DIR": str(tmp_path / "results"),
        "NAMESPACE": "test-namespace",
        "MLFLOW_TESTS_RUNTIME_IMAGE": "tests:fake",
        "OPERATOR_RUNTIME_IMAGE": "operator:fake",
        "MLFLOW_RUNTIME_IMAGE": "mlflow:fake",
        "BACKEND_STORE": "postgres",
        "REGISTRY_STORE": "postgres",
        "ARTIFACT_BACKENDS": backend,
        "SERVE_ARTIFACTS": "false",
        "ARTIFACTS_SERVER": "true",
        "ARTIFACTS_SERVER_GATEWAY": gateway,
        "AWS_ACCESS_KEY_ID": "fake-test-key",
        "AWS_SECRET_ACCESS_KEY": "fake-test-secret",
        "AWS_S3_BUCKET": "fake-test-bucket",
    }
    env.pop("CA_BUNDLE_PATH", None)
    result = subprocess.run(
        [bash_with_mapfile(), Path(__file__).with_name("run-integration-tests.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    args = docker_log.read_text().splitlines()
    assert f"ARTIFACT_BACKENDS={backend}" in docker_log.read_text()
    assert (
        "mlflow-artifacts.test-namespace.svc:127.0.0.1" in args
    ) == expect_artifact_host
    assert (
        "minio-service.test-namespace.svc.cluster.local:127.0.0.1" in args
    ) == expect_minio_host
