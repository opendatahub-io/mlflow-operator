import os
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from xml.etree.ElementTree import parse

import pytest
from write_harness_junit import harness_junit_path, write_harness_error_junit


def bash_with_mapfile() -> str:
    bash = shutil.which("bash")
    if (
        bash is None
        or subprocess.run(
            [bash, "-c", "type mapfile >/dev/null 2>&1"], check=False
        ).returncode
    ):
        pytest.skip("test-run.sh requires Bash with mapfile support")
    return bash


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _mlflow_delete_commands(kubectl_log: str) -> list[str]:
    return [
        line
        for line in kubectl_log.splitlines()
        if line.split()[:2] == ["delete", "mlflow"]
    ]


def test_harness_junit_path_includes_storage_type(tmp_path: Path) -> None:
    assert harness_junit_path(str(tmp_path), "file") == str(
        tmp_path / "xunit_report_file.xml"
    )
    assert harness_junit_path(str(tmp_path), None) == str(tmp_path / "xunit_report.xml")


def test_write_harness_error_junit_matches_pytest_shape(tmp_path: Path) -> None:
    output = tmp_path / "xunit_report_file.xml"
    wrote = write_harness_error_junit(
        str(output),
        suite_name="mlflow-e2e",
        test_name="test_wait_for_mlflow_server_info",
        message="MLflow server-info endpoint did not become reachable within timeout",
        body="storage=file backend=postgres\nURL: https://example/mlflow/api/3.0/mlflow/server-info",
        hostname="mlflow-tests",
    )

    assert wrote is True
    root = parse(output).getroot()
    assert root.tag == "testsuites"
    assert root.get("name") == "pytest tests"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.get("name") == "mlflow-e2e"
    assert suite.get("errors") == "1"
    assert suite.get("tests") == "1"
    case = suite.find("testcase")
    assert case is not None
    assert case.get("classname") == "tests.harness.TestHarnessSetup"
    assert case.get("name") == "test_wait_for_mlflow_server_info"
    error = case.find("error")
    assert error is not None
    assert "server-info" in (error.get("message") or "")
    assert "backend=postgres" in (error.text or "")


def test_write_harness_error_junit_does_not_overwrite_existing(tmp_path: Path) -> None:
    output = tmp_path / "xunit_report_file.xml"
    output.write_text(
        "<testsuites><testsuite name='mlflow-e2e'/></testsuites>", encoding="utf-8"
    )
    original = output.read_text(encoding="utf-8")

    wrote = write_harness_error_junit(
        str(output),
        test_name="test_deploy",
        message="deploy.py failed",
    )

    assert wrote is False
    assert output.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("overrides", "script_args", "expected_message"),
    [
        (
            {"ARTIFACTS_SERVER_GATEWAY": "true", "INFRASTRUCTURE_PLATFORM": "base"},
            [],
            "ARTIFACTS_SERVER_GATEWAY=true requires a Gateway-capable OpenShift cluster.",
        ),
        (
            {"ARTIFACTS_SERVER_GATEWAY": "true", "FORCE_PORT_FORWARD": "true"},
            [],
            "ARTIFACTS_SERVER_GATEWAY=true cannot use FORCE_PORT_FORWARD; the test must traverse the Gateway.",
        ),
        (
            {"BACKEND_STORE": "sqlite"},
            [],
            "ARTIFACTS_SERVER=true requires BACKEND_STORE=postgres and REGISTRY_STORE=postgres.",
        ),
        (
            {"ARTIFACT_BACKENDS": "gcs"},
            [],
            "Unsupported ARTIFACT_BACKENDS value: 'gcs'. Supported: file, s3, externals3.",
        ),
        (
            {"ARTIFACT_BACKENDS": ""},
            [],
            "ARTIFACT_BACKENDS must contain at least one of: file, s3, externals3.",
        ),
        (
            {"ARTIFACT_BACKENDS": "file,,s3"},
            [],
            "ARTIFACT_BACKENDS must not contain empty entries.",
        ),
        (
            {"ARTIFACT_BACKENDS": "externals3"},
            [],
            "externals3 requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and BUCKET.",
        ),
        (
            {"ARTIFACT_BACKENDS": "file,s3", "SKIP_CLEANUP": "true"},
            [],
            "SKIP_CLEANUP=true requires exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE.",
        ),
        (
            {"ARTIFACT_BACKENDS": "file,s3"},
            ["-m", "pre_upgrade"],
            "Upgrade pytest phases require exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE.",
        ),
        (
            {"ARTIFACT_BACKENDS": "file,s3", "SKIP_DEPLOYMENT": "true"},
            [],
            "SKIP_DEPLOYMENT=true requires exactly one backend matching the reused MLflow deployment.",
        ),
        (
            {"ARTIFACTS_SERVER": "false", "ARTIFACTS_SERVER_GATEWAY": "true"},
            [],
            "ARTIFACTS_SERVER_GATEWAY=true requires ARTIFACTS_SERVER=true.",
        ),
    ],
    ids=[
        "gateway-non-openshift",
        "gateway-port-forward",
        "sqlite-metadata",
        "unsupported-backend",
        "empty-backends",
        "empty-backend-entry",
        "external-s3-missing-credentials",
        "preserved-multi-backend",
        "upgrade-multi-backend",
        "reused-multi-backend",
        "gateway-without-server",
    ],
)
def test_invalid_artifacts_server_config_writes_harness_junit(
    tmp_path: Path,
    overrides: dict[str, str],
    script_args: list[str],
    expected_message: str,
) -> None:
    bash = bash_with_mapfile()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_kubectl.chmod(0o755)

    results_dir = tmp_path / "results"
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_RESULTS_DIR": str(results_dir),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "ARTIFACTS_SERVER": "true",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "INFRASTRUCTURE_PLATFORM": "openshift",
            "FORCE_PORT_FORWARD": "false",
            "SKIP_CLEANUP": "false",
            "SKIP_DEPLOYMENT": "false",
            "CLEANUP_REUSED_RESOURCES": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
            "ARTIFACT_BACKENDS": "s3",
            "AWS_ACCESS_KEY_ID": "",
            "AWS_SECRET_ACCESS_KEY": "",
            "BUCKET": "",
        }
    )
    env.update(overrides)

    result = subprocess.run(
        [bash, str(Path(__file__).with_name("test-run.sh")), *script_args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert expected_message in result.stderr
    reports = list(results_dir.glob("xunit_report*.xml"))
    assert len(reports) == 1
    test_case = parse(reports[0]).getroot().find("./testsuite/testcase")
    assert test_case is not None
    assert test_case.get("name") == "test_config"
    error = test_case.find("error")
    assert error is not None
    assert error.get("message") == expected_message


def test_artifacts_server_accepts_multi_backend_list_and_postgresql_alias(
    tmp_path: Path,
) -> None:
    bash = bash_with_mapfile()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    kubectl_log = tmp_path / "kubectl.log"

    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text(
        dedent(
            f"""\
            #!/bin/sh
            echo "$*" >> "{kubectl_log}"
            case "$*" in
                *"wait --for=condition=Available deployment/mlflow-artifacts"*) exit 1 ;;
            esac
            exit 0
            """
        ),
        encoding="utf-8",
    )
    fake_kubectl.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        dedent(
            """\
            #!/bin/sh
            while [ "$#" -gt 0 ]; do
                if [ "$1" = "-o" ]; then
                    printf '{}' > "$2"
                    shift 2
                else
                    shift
                fi
            done
            printf '200'
            """
        ),
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    results_dir = tmp_path / "results"
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_RESULTS_DIR": str(results_dir),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "ARTIFACTS_SERVER": "true",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "INFRASTRUCTURE_PLATFORM": "base",
            "FORCE_PORT_FORWARD": "false",
            "DEPLOY_MLFLOW_OPERATOR": "false",
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "true",
            "SKIP_INFRASTRUCTURE": "true",
            "SKIP_CLEANUP": "false",
            "FAIL_FAST": "false",
            "BACKEND_STORE": "postgresql",
            "REGISTRY_STORE": "postgresql",
            "ARTIFACT_BACKENDS": "file, s3",
            "workspaces": "test-workspace",
        }
    )

    result = subprocess.run(
        [bash, str(Path(__file__).with_name("test-run.sh"))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "mlflow-artifacts Deployment did not become available" in result.stderr
    assert "test_config" not in result.stderr
    assert len(_mlflow_delete_commands(kubectl_log.read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize(
    ("failure_mode", "expected_message"),
    [
        ("deployment", "mlflow-artifacts Deployment did not become available"),
        ("route", "mlflow-artifacts HTTPRoute was not accepted within timeout"),
        (
            "url",
            "MLflow CR status.artifactsUrl is empty with ARTIFACTS_SERVER=true",
        ),
    ],
    ids=["deployment", "route", "status-url"],
)
def test_artifacts_server_readiness_failure_writes_harness_junit(
    tmp_path: Path, failure_mode: str, expected_message: str
) -> None:
    bash = bash_with_mapfile()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text(
        dedent(
            """\
            #!/bin/sh
            case "$*" in
                *".spec.artifactsServer.enabled"*) printf 'true|false' ;;
                *"wait --for=condition=Available deployment/mlflow-artifacts"*)
                    [ "$ARTIFACT_FAILURE_MODE" = "deployment" ] && exit 1
                    ;;
                *"get httproute mlflow-artifacts"*)
                    if [ "$ARTIFACT_FAILURE_MODE" = "route" ]; then
                        printf 'Accepted=False\nResolvedRefs=False\n'
                    else
                        printf 'Accepted=True\nResolvedRefs=True\n'
                    fi
                    ;;
                *"jsonpath={.status.artifactsUrl}"*)
                    [ "$ARTIFACT_FAILURE_MODE" = "url" ] || printf 'https://mlflow.example/mlflow-artifacts'
                    ;;
                *"jsonpath={.status.url}"*)
                    printf 'https://mlflow.example/mlflow'
                    ;;
            esac
            exit 0
            """
        ),
        encoding="utf-8",
    )
    fake_kubectl.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        dedent(
            """\
            #!/bin/sh
            output_file=""
            while [ "$#" -gt 0 ]; do
                if [ "$1" = "-o" ]; then
                    output_file="$2"
                    shift 2
                else
                    shift
                fi
            done
            [ -z "$output_file" ] || printf '{}' > "$output_file"
            printf '200'
            """
        ),
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)

    results_dir = tmp_path / "results"
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_RESULTS_DIR": str(results_dir),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "ARTIFACTS_SERVER": "true",
            "ARTIFACTS_SERVER_GATEWAY": "true",
            "ARTIFACT_FAILURE_MODE": failure_mode,
            "INFRASTRUCTURE_PLATFORM": "openshift",
            "FORCE_PORT_FORWARD": "false",
            "SKIP_DEPLOYMENT": "true",
            "SKIP_INFRASTRUCTURE": "true",
            "SKIP_CLEANUP": "true",
            "CLEANUP_REUSED_RESOURCES": "false",
            "BACKEND_STORE": "postgres",
            "REGISTRY_STORE": "postgres",
            "ARTIFACT_BACKENDS": "s3",
            "workspaces": "test-workspace",
        }
    )

    result = subprocess.run(
        [bash, str(Path(__file__).with_name("test-run.sh"))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert expected_message in result.stderr
    reports = list(results_dir.glob("xunit_report*.xml"))
    assert len(reports) == 1
    test_case = parse(reports[0]).getroot().find("./testsuite/testcase")
    assert test_case is not None
    assert test_case.get("name") == "test_wait_for_artifacts_server_route"
    error = test_case.find("error")
    assert error is not None
    assert error.get("message") == expected_message


@pytest.mark.parametrize(
    ("overrides", "expected_returncode", "expect_delete"),
    [
        ({"UV_PYTEST_EXIT": "0"}, 0, True),
        ({"UV_PYTEST_EXIT": "1"}, 1, True),
        ({"CURL_STATUS": "200", "CURL_EXIT": "28", "UV_PYTEST_EXIT": "0"}, 1, True),
        ({"CURL_STATUS": "503", "UV_PYTEST_EXIT": "0"}, 1, True),
        ({"CURL_FAIL_ONCE": "true", "UV_PYTEST_EXIT": "0"}, 0, True),
        (
            {
                "ARTIFACT_BACKENDS": "file,s3",
                "UV_DEPLOY_FAIL_S3": "true",
                "UV_PYTEST_EXIT": "0",
            },
            1,
            True,
        ),
        (
            {
                "ARTIFACT_BACKENDS": "file,externals3",
                "UV_DEPLOY_FAIL_S3": "true",
                "UV_PYTEST_EXIT": "0",
            },
            1,
            True,
        ),
        ({"UV_DEPLOY_EXIT": "1"}, 1, True),
        ({"HARNESS_SIGNAL": "INT"}, 130, True),
        ({"HARNESS_SIGNAL": "TERM"}, 143, True),
        ({"SKIP_CLEANUP": "true"}, 1, False),
        (
            {
                "SKIP_DEPLOYMENT": "true",
                "SKIP_CLEANUP": "false",
                "CLEANUP_REUSED_RESOURCES": "false",
            },
            1,
            False,
        ),
        (
            {
                "SKIP_DEPLOYMENT": "true",
                "SKIP_CLEANUP": "false",
                "CLEANUP_REUSED_RESOURCES": "true",
            },
            1,
            True,
        ),
        (
            {
                "SKIP_DEPLOYMENT": "true",
                "SKIP_CLEANUP": "false",
                "CLEANUP_REUSED_RESOURCES": "on_success",
                "UV_PYTEST_EXIT": "0",
            },
            0,
            True,
        ),
        (
            {
                "SKIP_DEPLOYMENT": "true",
                "SKIP_CLEANUP": "false",
                "CLEANUP_REUSED_RESOURCES": "on_success",
            },
            1,
            False,
        ),
    ],
    ids=[
        "last-suite-success",
        "last-suite-failed-pytest",
        "http-200-transfer-timeout",
        "http-503-successful-transfer",
        "successful-retry-after-transfer-timeout",
        "s3-failure-after-file-success",
        "external-s3-failure-after-file-success",
        "partial-deploy-failure",
        "interrupt",
        "terminate",
        "skip-cleanup",
        "reuse-preserve",
        "reuse-cleanup",
        "reuse-cleanup-on-success",
        "reuse-preserve-on-failure",
    ],
)
def test_last_suite_deletes_cluster_scoped_mlflow_cr(
    tmp_path: Path,
    overrides: dict[str, str],
    expected_returncode: int,
    expect_delete: bool,
) -> None:
    bash = bash_with_mapfile()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    kubectl_log = tmp_path / "kubectl.log"
    uv_log = tmp_path / "uv.log"

    _write_executable(
        fake_bin / "kubectl",
        dedent(
            f"""\
            #!/bin/sh
            echo "$*" >> "{kubectl_log}"
            case "$*" in
                *".spec.artifactsServer.enabled"*) printf 'false|true' ;;
                *"create token"*)
                    printf 'fake-token\\n'
                    ;;
                *"jsonpath={{.status.url}}"*)
                    printf 'https://mlflow.example/mlflow'
                    ;;
                *"jsonpath={{.spec.traceArchival.enabled}}"*)
                    printf 'false'
                    ;;
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
            output_file=""
            while [ "$#" -gt 0 ]; do
                if [ "$1" = "-o" ]; then
                    output_file="$2"
                    shift 2
                else
                    shift
                fi
            done
            [ -z "$output_file" ] || printf 'file storage response' > "$output_file"
            printf '%s' "${CURL_STATUS:-200}"
            if [ "${CURL_FAIL_ONCE:-false}" = true ] && [ ! -f "$TEST_RESULTS_DIR/curl-retried" ]; then
                touch "$TEST_RESULTS_DIR/curl-retried"
                exit 28
            fi
            exit "${CURL_EXIT:-0}"
            """
        ),
    )
    _write_executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "uv",
        dedent(
            """\
            #!/bin/sh
            case "$*" in
                *deploy.py*)
                    if [ "${UV_DEPLOY_FAIL_S3:-false}" = true ]; then
                        case "$*" in
                            *"--artifact-storage s3"*|*"--artifact-storage externals3"*) exit 7 ;;
                        esac
                    fi
                    exit "${UV_DEPLOY_EXIT:-0}"
                    ;;
                *pytest*)
                    for arg in "$@"; do
                        case "$arg" in
                            --junit-xml=*) printf '<testsuites><testsuite name="pytest-original"/></testsuites>' > "${arg#*=}" ;;
                        esac
                    done
                    touch "$TEST_RESULTS_DIR/pytest-started"
                    if [ -n "${HARNESS_SIGNAL:-}" ]; then
                        trap 'echo child-stopped >> "$UV_LOG"; exit 0' TERM
                        echo child-started >> "$UV_LOG"
                        kill -s "${HARNESS_SIGNAL}" "$PPID"
                        while true; do sleep 1; done
                    fi
                    exit "${UV_PYTEST_EXIT:-1}"
                    ;;
            esac
            exit 0
            """
        ),
    )

    results_dir = tmp_path / "results"
    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_RESULTS_DIR": str(results_dir),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "ARTIFACTS_SERVER": "false",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "INFRASTRUCTURE_PLATFORM": "openshift",
            "FORCE_PORT_FORWARD": "false",
            "DEPLOY_MLFLOW_OPERATOR": "false",
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "true",
            "SKIP_INFRASTRUCTURE": "true",
            "SKIP_CLEANUP": "false",
            "CLEANUP_REUSED_RESOURCES": "false",
            "BACKEND_STORE": "sqlite",
            "REGISTRY_STORE": "sqlite",
            "ARTIFACT_BACKENDS": "s3",
            "workspaces": "test-workspace",
            "UV_LOG": str(uv_log),
            "AWS_ACCESS_KEY_ID": "test-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "BUCKET": "test-bucket",
        }
    )
    env.update(overrides)
    if "UV_DEPLOY_FAIL_S3" in overrides:
        shared_debug = results_dir / "debug"
        shared_debug.mkdir(parents=True)
        (shared_debug / "server-info-last-body.txt").write_text("stale shared response")
        (shared_debug / "failure-snapshot.txt").write_text("stale shared snapshot")

    result = subprocess.run(
        [bash, str(Path(__file__).with_name("test-run.sh"))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == expected_returncode, result.stdout + result.stderr
    if "CURL_STATUS" in overrides:
        assert not (results_dir / "pytest-started").exists()
        case = (
            parse(results_dir / "xunit_report_s3.xml")
            .getroot()
            .find("./testsuite/testcase")
        )
        assert case.get("name") == "test_wait_for_mlflow_server_info"
        assert f"last_curl_exit={overrides.get('CURL_EXIT', '0')}" in case.find(
            "error"
        ).get("message")
    if "CURL_FAIL_ONCE" in overrides:
        assert (results_dir / "pytest-started").exists()
        assert "HTTP=200 curl_exit=28" in result.stdout
        assert "MLflow server-info endpoint is reachable (HTTP 200)" in result.stdout
    if "UV_DEPLOY_FAIL_S3" in overrides:
        storage = overrides["ARTIFACT_BACKENDS"].split(",")[-1]
        current_debug = results_dir / "debug" / storage
        assert (
            results_dir / "debug/file/server-info-last-body.txt"
        ).read_text() == "file storage response"
        assert (current_debug / "namespaces.txt").exists()
        snapshot = (current_debug / "failure-snapshot.txt").read_text()
        case = (
            parse(results_dir / f"xunit_report_{storage}.xml")
            .getroot()
            .find("./testsuite/testcase")
        )
        assert case.get("name") == "test_deploy"
        for text in (snapshot, case.find("error").text):
            assert "file storage response" not in text
            assert "stale shared" not in text
        assert (results_dir / "xunit_report_file.xml").exists()
    log_text = kubectl_log.read_text(encoding="utf-8") if kubectl_log.exists() else ""
    deletes = _mlflow_delete_commands(log_text)
    if expect_delete:
        expected_deletes = 2 if "UV_DEPLOY_FAIL_S3" in overrides else 1
        assert len(deletes) == expected_deletes, (
            f"expected {expected_deletes} MLflow CR deletions, kubectl log:\n"
            f"{log_text}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "--wait" in deletes[0]
        assert all("-n" not in line.split() for line in deletes)
        assert "Deleting cluster-scoped MLflow CR mlflow" in result.stdout
        if expected_returncode != 0:
            log_lines = log_text.splitlines()
            last_delete_index = max(
                i for i, line in enumerate(log_lines) if line == deletes[-1]
            )
            assert last_delete_index > next(
                i for i, line in enumerate(log_lines) if line == "get namespaces"
            ), "failure diagnostics must be collected before deleting the MLflow CR"
        if "HARNESS_SIGNAL" in overrides:
            assert uv_log.read_text(encoding="utf-8").splitlines() == [
                "child-started",
                "child-stopped",
            ]
    else:
        assert deletes == [], (
            f"unexpected MLflow CR deletion: {deletes}\nstderr:\n{result.stderr}"
        )


def test_mlflow_delete_failure_stops_before_next_backend(tmp_path: Path) -> None:
    bash = bash_with_mapfile()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    kubectl_log = tmp_path / "kubectl.log"
    uv_log = tmp_path / "uv.log"

    _write_executable(
        fake_bin / "kubectl",
        dedent(
            f"""\
            #!/bin/sh
            echo "$*" >> "{kubectl_log}"
            case "$*" in
                "delete mlflow "*) exit 1 ;;
                *"create token"*) printf 'fake-token\\n' ;;
                *"jsonpath={{.status.url}}"*) printf 'https://mlflow.example/mlflow' ;;
                *"jsonpath={{.spec.traceArchival.enabled}}"*) printf 'false' ;;
            esac
            exit 0
            """
        ),
    )
    _write_executable(
        fake_bin / "curl",
        '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = -o ] && { printf \'{}\' > "$2"; shift; }; shift; done\nprintf \'200\'\n',
    )
    _write_executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "uv",
        '#!/bin/sh\necho "$*" >> "$UV_LOG"\nexit 0\n',
    )

    env = os.environ.copy()
    env.pop("DB_TYPE", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TEST_RESULTS_DIR": str(tmp_path / "results"),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.14",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.14.0",
            "ARTIFACTS_SERVER": "false",
            "ARTIFACTS_SERVER_GATEWAY": "false",
            "INFRASTRUCTURE_PLATFORM": "openshift",
            "FORCE_PORT_FORWARD": "false",
            "DEPLOY_MLFLOW_OPERATOR": "false",
            "SKIP_DEPLOYMENT": "false",
            "SKIP_OPERATOR": "true",
            "SKIP_INFRASTRUCTURE": "true",
            "SKIP_CLEANUP": "false",
            "FAIL_FAST": "false",
            "BACKEND_STORE": "sqlite",
            "REGISTRY_STORE": "sqlite",
            "ARTIFACT_BACKENDS": "file,s3",
            "workspaces": "test-workspace",
            "UV_LOG": str(uv_log),
        }
    )

    result = subprocess.run(
        [bash, str(Path(__file__).with_name("test-run.sh"))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 1
    assert "failed to delete MLflow CR mlflow" in result.stderr
    deploys = [
        line
        for line in uv_log.read_text(encoding="utf-8").splitlines()
        if "deploy.py" in line
    ]
    assert len(deploys) == 1, (
        "the next backend must not start after MLflow CR deletion fails"
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "\x00",
        "\x01",
        "\x0b",
        "\x1b",
        "\x1f",
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udce2", id="truncated-utf8-surrogate"),
        "\ufffe",
        "\uffff",
    ],
)
def test_diagnostics_are_xml_safe(tmp_path: Path, invalid: str) -> None:
    output = tmp_path / "xunit_report_file.xml"
    valid = '\t\né中😀<&>"\ud7ff\ue000\ufffd\U0010ffff'
    diagnostics = f"before{invalid}after{valid}"
    expected = f"before\ufffdafter{valid}"

    assert write_harness_error_junit(
        str(output),
        suite_name=diagnostics,
        classname=diagnostics,
        test_name=diagnostics,
        message=diagnostics,
        body=diagnostics,
        hostname=diagnostics,
    )

    suite = parse(output).getroot().find("testsuite")
    case = suite.find("testcase")
    error = case.find("error")
    assert suite.get("name") == expected
    assert suite.get("hostname") == expected
    assert case.get("classname") == expected
    assert case.get("name") == expected
    assert error.get("message") == expected
    assert error.text == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("-o", "junit_suite_name=MLflow E2E"), "MLflow E2E"),
        (("--override-ini", "junit_suite_name=MLflow E2E"), "MLflow E2E"),
        (("--override-ini=junit_suite_name=MLflow E2E",), "MLflow E2E"),
        (("-ojunit_suite_name=MLflow E2E",), "MLflow E2E"),
        (("-o=junit_suite_name=MLflow E2E",), "MLflow E2E"),
        (("-k", "junit_suite_name=unrelated"), "mlflow-e2e"),
    ],
)
def test_bootstrap_report_preserves_suite_name(
    tmp_path: Path, args: tuple[str, ...], expected: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "kubectl", "#!/bin/sh\nexit 0\n")
    results_dir = tmp_path / "results"
    result = subprocess.run(
        [bash_with_mapfile(), str(Path(__file__).with_name("test-run.sh")), *args],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TEST_RESULTS_DIR": str(results_dir),
            "MLFLOW_TEST_SUPPORTED_VERSION": "3.15",
            "SUPPORTED_MLFLOW_VERSION_RAW": "3.15.0",
            "DB_TYPE": "invalid",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    suite = parse(results_dir / "xunit_report.xml").getroot().find("testsuite")
    assert suite.get("name") == expected
    assert (results_dir / "debug/bootstrap/failure-snapshot.txt").exists()
