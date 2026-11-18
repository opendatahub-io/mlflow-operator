"""Hermetic tests for prepopulation helpers, evidence validation, and log hygiene."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from kubernetes import client as k8s_client
from urllib3 import HTTPResponse

from .actions import trace_prepopulation_actions
from .actions.trace_prepopulation_actions import (
    _database_query,
    _parse_prepopulation_output,
    _parse_trace_row_states,
)
from .validations import trace_prepopulation_validations
from . import test_trace_analytics_prepopulation as prepopulation_suite


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def _mock_query_process(
    monkeypatch: pytest.MonkeyPatch, stdout: str = "", exit_code: int = 0,
):
    run = Mock(return_value=subprocess.CompletedProcess(
        args=["kubectl"], returncode=exit_code, stdout=stdout, stderr="",
    ))
    monkeypatch.setattr(trace_prepopulation_actions.subprocess, "run", run)
    return run


_DATABASE_STATE = {
    "database_pod": "postgres-0",
    "database_user": "mlflow",
    "database": "mydatabase",
}


@pytest.mark.parametrize("failure", [None, "action", "missing-id", "missing-cleanup"])
def test_prepopulation_experiment_validation_without_active_user(
    failure: str | None,
) -> None:
    context = SimpleNamespace(
        active_user=None,
        last_error=None,
        active_experiment_id="experiment-1",
        experiments_to_delete=["experiment-1"],
    )
    if failure == "action":
        context.last_error = SimpleNamespace(
            error=SimpleNamespace(code="TEST_FAILURE", message="creation failed")
        )
    elif failure == "missing-id":
        context.active_experiment_id = None
    elif failure == "missing-cleanup":
        context.experiments_to_delete = []

    step = next(
        step
        for step in prepopulation_suite.TestTraceAnalyticsPrepopulation.test_scenarios[0].test_steps
        if step.action_func is prepopulation_suite.action_create_experiment
    )
    if failure is None:
        step.validate_func(context)
    else:
        expected = {
            "action": "TEST_FAILURE",
            "missing-id": "experiment was not created",
            "missing-cleanup": "not recorded for cleanup",
        }
        with pytest.raises(AssertionError, match=expected[failure]):
            step.validate_func(context)


def test_database_query_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _mock_query_process(monkeypatch, stdout="42\n")

    result = _database_query(Mock(), _DATABASE_STATE, "SELECT 1")

    assert result == "42"
    assert run.call_args.kwargs["timeout"] == trace_prepopulation_actions.DATABASE_QUERY_TIMEOUT_SECONDS
    assert run.call_args.kwargs["capture_output"] is True
    assert run.call_args.kwargs["text"] is True
    assert run.call_args.args[0][:7] == [
        "kubectl", "exec", "-i", "-n",
        trace_prepopulation_actions._operator_namespace(), "postgres-0", "--",
    ]


def test_database_query_fails_closed_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_query_process(monkeypatch, stdout="ERROR: something failed", exit_code=1)

    with pytest.raises(pytest.fail.Exception, match=r"psql exit 1"):
        _database_query(Mock(), _DATABASE_STATE, "SELECT 1")


def test_database_query_fails_closed_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _mock_query_process(monkeypatch)
    run.side_effect = subprocess.TimeoutExpired(
        cmd=["kubectl", "sensitive-query"], timeout=1,
        output=b"sensitive-row", stderr=b"sensitive-error",
    )

    with pytest.raises(pytest.fail.Exception, match="timed out during connection setup or execution"):
        _database_query(Mock(), _DATABASE_STATE, "SELECT 1")


def test_database_query_does_not_return_error_text_as_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failed exec must not hand raw error output to callers: the fixed
    # failure message is the only output (H1 log hygiene for exec evidence).
    run = _mock_query_process(monkeypatch, stdout="ERROR: permission denied", exit_code=1)
    run.return_value.stderr = "sensitive-error"

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _database_query(Mock(), _DATABASE_STATE, "SELECT 1")

    assert "permission denied" not in str(excinfo.value)
    assert "sensitive-error" not in str(excinfo.value)


def test_database_query_terminates_stalled_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    executable = tmp_path / "kubectl"
    pid_file = tmp_path / "pid"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "print('private-query-output', flush=True)\n"
        "print('private-query-error', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(trace_prepopulation_actions, "DATABASE_QUERY_TIMEOUT_SECONDS", 1)
    started = time.monotonic()

    with pytest.raises(pytest.fail.Exception, match="timed out during connection setup"):
        _database_query(Mock(), _DATABASE_STATE, "SELECT 1")

    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    captured = capsys.readouterr()
    assert "private-query-output" not in captured.out + captured.err
    assert "private-query-error" not in captured.out + captured.err


@pytest.mark.parametrize("error", [FileNotFoundError, UnicodeDecodeError])
def test_database_query_sanitizes_launch_and_decode_errors(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception],
) -> None:
    run = _mock_query_process(monkeypatch)
    run.side_effect = (
        FileNotFoundError("sensitive-command") if error is FileNotFoundError
        else UnicodeDecodeError("utf-8", b"sensitive-output", 0, 1, "invalid")
    )
    with pytest.raises(pytest.fail.Exception, match="Could not execute the database query") as exc:
        _database_query(Mock(), _DATABASE_STATE, "SELECT 1")
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_database_query_failure_hides_output_in_pytest_reports(
    tmp_path: Path, failure: str,
) -> None:
    project = Path(trace_prepopulation_actions.__file__).resolve().parents[2]
    probe = tmp_path / "test_query_probe.py"
    probe.write_text(
        "import sys, subprocess\n"
        f"sys.path.insert(0, {str(project)!r})\n"
        "from tests.actions import trace_prepopulation_actions as actions\n"
        "def test_probe(monkeypatch):\n"
        "    sentinel = ''.join(['RAW_QUERY_', 'OUTPUT_7f3a'])\n"
        "    def run(*args, **kwargs):\n"
        f"        if {failure!r} == 'timeout':\n"
        "            raise subprocess.TimeoutExpired(['kubectl', sentinel], 1, "
        "output=sentinel, stderr=sentinel)\n"
        "        return subprocess.CompletedProcess(['kubectl'], 1, sentinel, sentinel)\n"
        "    monkeypatch.setattr(actions.subprocess, 'run', run)\n"
        "    actions._database_query(None, "
        "{'database_pod': 'postgres-0', 'database_user': 'test', 'database': 'test'}, "
        "'SELECT 1')\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         f"--junit-xml={junit}", str(probe)],
        capture_output=True, text=True, cwd=project, env=env, timeout=30,
    )
    assert result.returncode == 1
    assert "Database query" in result.stdout
    assert "RAW_QUERY_OUTPUT_7f3a" not in result.stdout + result.stderr + junit.read_text()


def _psql_assignments(command: list) -> set[str]:
    """Collect the ``-v name=value`` assignments from an exec command list."""
    return {
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "-v" and index + 1 < len(command)
    }


def test_database_query_binds_values_as_psql_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _mock_query_process(monkeypatch, stdout="1\n")

    sql = (
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name = :'v0' AND column_name = :'v1'"
    )
    result = _database_query(
        Mock(), _DATABASE_STATE, sql, params={"v0": "trace_info", "v1": "trace_name"}
    )

    assert result == "1"
    command = run.call_args.args[0]
    assert command[command.index("--") + 1] == "psql"
    assert _psql_assignments(command) >= {"v0=trace_info", "v1=trace_name"}
    # The SQL rides on stdin, terminated by \q, and carries placeholders only.
    assert run.call_args.kwargs["input"] == sql + "\n\\q\n"
    assert "trace_info" not in run.call_args.kwargs["input"]
    assert "trace_name" not in run.call_args.kwargs["input"]


def test_analytics_column_exists_binds_filter_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _mock_query_process(monkeypatch, stdout="1\n")

    assert trace_prepopulation_actions._analytics_column_exists(
        Mock(), _DATABASE_STATE, "trace_info", "trace_name"
    )

    sql = run.call_args.kwargs["input"]
    assert ":'v0'" in sql and ":'v1'" in sql
    assert "trace_name" not in sql
    assert _psql_assignments(run.call_args.args[0]) >= {
        "v0=trace_info",
        "v1=trace_name",
    }


def test_trace_row_states_binds_trace_ids_out_of_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _mock_query_process(monkeypatch, stdout="trace-a:100\ntrace-b:101\n")

    states = trace_prepopulation_actions._trace_row_states(
        Mock(), _DATABASE_STATE, ["trace-a", "trace-b"]
    )

    assert states == {"trace-a": 100, "trace-b": 101}
    sql = run.call_args.kwargs["input"]
    assert ":'v0'" in sql and ":'v1'" in sql
    assert "trace-a" not in sql and "trace-b" not in sql
    assert _psql_assignments(run.call_args.args[0]) >= {
        "v0=trace-a",
        "v1=trace-b",
    }


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "non_numeric", "unexpected"])
def test_span_row_states_requires_complete_owned_evidence(
    monkeypatch: pytest.MonkeyPatch, fault: str | None,
) -> None:
    rows = ["trace-a/root:100", "trace-a/child:101"]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "non_numeric":
        rows[0] = "trace-a/root:invalid"
    elif fault == "unexpected":
        rows.append("unrelated/span:102")
    run = _mock_query_process(monkeypatch, stdout="\n".join(rows))
    payloads = [{"trace_id": "trace-a", "root_span_id": "root", "child_span_id": "child"}]

    def read_states():
        return trace_prepopulation_actions._span_row_states(Mock(), _DATABASE_STATE, payloads)

    if fault is None:
        assert read_states() == {"trace-a/root": 100, "trace-a/child": 101}
    else:
        with pytest.raises(pytest.fail.Exception, match="row-state evidence"):
            read_states()
    assert "trace-a" not in run.call_args.kwargs["input"]
    assert "FROM spans" in run.call_args.kwargs["input"]
    assert "v0=trace-a" in _psql_assignments(run.call_args.args[0])


def test_parse_trace_row_states_accepts_complete_evidence() -> None:
    rows = "trace-a:100\ntrace-b:4294967295\n"

    states = _parse_trace_row_states(rows, ["trace-a", "trace-b"])

    assert states == {"trace-a": 100, "trace-b": 4294967295}


def test_parse_trace_row_states_fails_on_missing_trace() -> None:
    with pytest.raises(pytest.fail.Exception, match="incomplete"):
        _parse_trace_row_states("trace-a:100\n", ["trace-a", "trace-b"])


def test_parse_trace_row_states_fails_on_empty_evidence() -> None:
    with pytest.raises(pytest.fail.Exception, match="incomplete"):
        _parse_trace_row_states("", ["trace-a"])


def test_parse_trace_row_states_fails_on_non_numeric_xmin() -> None:
    with pytest.raises(pytest.fail.Exception, match="non-numeric xmin"):
        _parse_trace_row_states("trace-a:not-a-number\n", ["trace-a"])


def test_parse_trace_row_states_fails_on_duplicate_rows() -> None:
    with pytest.raises(pytest.fail.Exception, match="duplicate"):
        _parse_trace_row_states("trace-a:100\ntrace-a:101\n", ["trace-a"])


def test_parse_trace_row_states_fails_on_unexpected_rows() -> None:
    with pytest.raises(pytest.fail.Exception, match="unexpected"):
        _parse_trace_row_states("trace-a:100\ntrace-b:200\n", ["trace-a"])


def test_parse_trace_row_states_ignores_separator_free_lines() -> None:
    # Column headers or empty lines without a separator are skipped, not
    # misparsed as evidence.
    states = _parse_trace_row_states("header\n\ntrace-a:100\n", ["trace-a"])

    assert states == {"trace-a": 100}


ELIGIBILITY_SCRIPT = (
    Path(trace_prepopulation_actions.__file__).resolve().parents[2]
    / "images"
    / "prepopulation-eligibility.sh"
)

DEBUG_LOG_COLLECTOR = (
    Path(trace_prepopulation_actions.__file__).resolve().parents[2]
    / "images"
    / "collect-debug-logs.sh"
)

_FAKE_KUBECTL = r"""#!/bin/bash
# Fake kubectl for the debug-log collector regression test. Returns a
# prepopulation Job pod for the unfiltered app=mlflow selector (so a
# regression to raw collection is caught) and only the MLflow server pod for
# the component-excluding selector the collector is supposed to use.
set -u
case "$*" in
    *"get namespace test-ns"*) exit 0 ;;
    *"get namespaces"*) exit 0 ;;
esac
if [[ "$*" == *"get pods"* && "$*" == *"-l"* ]]; then
    if [[ "$*" == *"component!=mlflow-trace-analytics-prepopulation"* ]]; then
        echo "mlflow-server-abc"
        exit 0
    fi
    if [[ "$*" == *" -l app=mlflow "* ]]; then
        echo "mlflow-trace-analytics-prepopulation-xyz"
        exit 0
    fi
    exit 0
fi
if [[ "$*" == *"logs mlflow-trace-analytics-prepopulation"* ]]; then
    echo "RAW_PREPOPULATION_LOG_SENTINEL_91c4"
    exit 0
fi
if [[ "$*" == *"logs mlflow-server-abc"* ]]; then
    echo "normal mlflow server log line"
    exit 0
fi
exit 0
"""


def test_debug_log_collector_excludes_prepopulation_pod_logs(
    tmp_path: Path,
) -> None:
    """The shared app=mlflow selector must exclude raw prepopulation pod logs."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text(_FAKE_KUBECTL, encoding="utf-8")
    fake_kubectl.chmod(0o755)
    output_dir = tmp_path / "debug"

    result = subprocess.run(
        [
            "bash",
            str(DEBUG_LOG_COLLECTOR),
            "--namespace",
            "test-ns",
            "--output-dir",
            str(output_dir),
        ],
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (output_dir / "mlflow-server-abc.log").exists()
    prepopulation_artifacts = sorted(
        path.name for path in output_dir.glob("*prepopulation*")
    )
    assert prepopulation_artifacts == []
    for artifact in output_dir.iterdir():
        if artifact.is_file():
            assert "RAW_PREPOPULATION_LOG_SENTINEL_91c4" not in artifact.read_text(
                encoding="utf-8"
            )


def _eligibility(env: dict[str, str]) -> str:
    result = subprocess.run(
        ["bash", str(ELIGIBILITY_SCRIPT)],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_prepopulation_eligibility_accepts_both_postgres_spellings() -> None:
    for backend in ("postgres", "postgresql"):
        verdict = _eligibility(
            {"BACKEND_STORE": backend, "TRACE_PREPOPULATION_IMAGE": "img:1",
             "POSTGRES_TLS": "true"}
        )

        assert verdict == "eligible", backend


def test_prepopulation_eligibility_matrix_rejects_ineligible_runs() -> None:
    image = {"TRACE_PREPOPULATION_IMAGE": "img:1"}
    cases = [
        ({**image, "BACKEND_STORE": "sqlite"}, "not self-deployed PostgreSQL"),
        ({**image, "BACKEND_STORE": "postgresql", "SKIP_DEPLOYMENT": "true"}, "SKIP_DEPLOYMENT"),
        ({**image, "BACKEND_STORE": "postgres", "SKIP_INFRASTRUCTURE": "true"}, "SKIP_INFRASTRUCTURE"),
        ({**image, "BACKEND_STORE": "postgres", "DB_HOST": "db.example"}, "external database"),
        ({"BACKEND_STORE": "postgres"}, "TRACE_PREPOPULATION_IMAGE not set"),
        ({"POSTGRES_TLS": "true"}, "TRACE_PREPOPULATION_IMAGE not set"),
        ({}, "TRACE_PREPOPULATION_IMAGE not set"),
    ]
    for env, expected_reason in cases:
        verdict = _eligibility(env)

        assert verdict.startswith("ineligible:"), env
        assert expected_reason in verdict, env


def _harness_eligibility_invocation(harness: str) -> str:
    return harness[
        harness.index("    if ! prepopulation_eligibility="):
        harness.index('    if [ "$prepopulation_eligibility"')
    ]


@pytest.mark.parametrize("db_type", ["postgres", "postgresql"])
def test_harness_forwards_legacy_backend_to_eligibility(db_type: str) -> None:
    harness = (ELIGIBILITY_SCRIPT.parent / "test-run.sh").read_text()
    normalization = harness[
        harness.index("# Legacy DB_TYPE support:"):
        harness.index("# When true (default)")
    ]
    invocation = _harness_eligibility_invocation(harness)
    result = subprocess.run(
        [
            "bash", "-eu", "-c",
            normalization + invocation + '\nprintf "%s" "$prepopulation_eligibility"',
        ],
        env={
            "PATH": "/usr/bin:/bin",
            "DB_TYPE": db_type,
            "TRACE_PREPOPULATION_IMAGE": "img:1",
            "SCRIPT_DIR": str(ELIGIBILITY_SCRIPT.parent),
            "POSTGRES_TLS": "false",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "eligible"


@pytest.mark.parametrize("failure", ["missing", "nonzero"])
def test_harness_fails_closed_on_eligibility_errors(
    tmp_path: Path, failure: str,
) -> None:
    harness = (ELIGIBILITY_SCRIPT.parent / "test-run.sh").read_text()
    script_dir = tmp_path
    if failure == "nonzero":
        (script_dir / "prepopulation-eligibility.sh").write_text("exit 2\n")
    events = tmp_path / "events"
    script = (
        'fail_suite() { echo failure >> "$EVENTS"; }\n'
        'restore_test_ca_bundle_environment() { echo restore >> "$EVENTS"; }\n'
        'run_suite_body() {\n'
        + _harness_eligibility_invocation(harness)
        + '\necho unexpected-continuation >> "$EVENTS"; }\n'
        'status=0\nrun_suite_body || status=$?\nexit "$status"\n'
    )
    result = subprocess.run(
        ["bash", "-eu", "-c", script],
        env={
            "PATH": "/usr/bin:/bin", "EVENTS": str(events),
            "BACKEND_STORE": "postgres", "TRACE_PREPOPULATION_IMAGE": "img:1",
            "SCRIPT_DIR": str(script_dir),
        },
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert events.read_text().splitlines() == ["failure", "restore"]


def test_workflow_source_is_packaged_in_tests_image() -> None:
    dockerfile = (ELIGIBILITY_SCRIPT.parent / "Dockerfile.konflux").read_text()
    path = ".github/workflows/trace-analytics-prepopulation.yml"
    assert (ELIGIBILITY_SCRIPT.parents[2] / path).is_file()
    assert any(
        line.startswith("COPY ") and line.split()[-2:] == [path, "./" + path]
        for line in dockerfile.splitlines()
    )


def test_workflow_source_is_in_build_context() -> None:
    dockerignore_path = ELIGIBILITY_SCRIPT.parents[2] / ".dockerignore"
    if not dockerignore_path.is_file():
        pytest.skip("Build-context checks require a full repository checkout")
    path = ".github/workflows/trace-analytics-prepopulation.yml"
    dockerignore = dockerignore_path.read_text()
    assert "!" + path in dockerignore.splitlines()


@pytest.mark.parametrize("quiescence_fails", [False, True])
def test_workflow_stops_operator_before_switching_runtime(
    tmp_path: Path, quiescence_fails: bool
) -> None:
    workflow_path = (
        ELIGIBILITY_SCRIPT.parents[2]
        / ".github/workflows/trace-analytics-prepopulation.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text())
    step = next(
        step
        for step in workflow["jobs"]["prepopulation-validation"]["steps"]
        if step["name"] == "Upgrade the prepopulated deployment in place"
    )
    script = step["run"]
    for name in ("NAMESPACE", "MLFLOW_RUNTIME_IMAGE", "OPERATOR_RUNTIME_IMAGE"):
        script = script.replace("${{ env." + name + " }}", workflow["env"][name])
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    commands = {
        "kubectl": '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [[ "$*" == "wait --for=delete "* && "$FAIL_QUIESCENCE" == true ]]; then exit 1; fi\n',
        "python3": '#!/bin/bash\ncase "$1" in\n'
        'scripts/print_supported_mlflow_version.py) echo 3.17.0 ;;\n'
        '.github/actions/deploy/deploy.py) echo deploy-current-operator >> "$CALL_LOG" ;;\n'
        '*) exit 2 ;;\nesac\n',
    }
    for name, contents in commands.items():
        executable = fake_bin / name
        executable.write_text(contents)
        executable.chmod(0o755)
    call_log = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "-eu", "-c", script],
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CALL_LOG": str(call_log),
            "FAIL_QUIESCENCE": str(quiescence_fails).lower(),
        },
        capture_output=True,
        text=True,
    )
    calls = call_log.read_text().splitlines()
    assert calls[0].startswith(
        "scale deployment/mlflow-operator-controller-manager --replicas=0"
    )
    assert calls[1].startswith(
        "wait --for=delete pod -l control-plane=controller-manager"
    )
    if quiescence_fails:
        assert result.returncode != 0
        assert len(calls) == 2
    else:
        assert result.returncode == 0, result.stderr
        assert calls[2].startswith("patch mlflow mlflow")
        assert workflow["env"]["MLFLOW_RUNTIME_IMAGE"] in calls[2]
        assert calls[3] == "deploy-current-operator"


def test_seed_trace_retains_spans_for_explicit_otlp_ingestion() -> None:
    admin = Mock()
    root = SimpleNamespace(request_id="trace-a", span_id="root")
    child = SimpleNamespace(span_id="child")
    admin.start_trace.return_value = root
    admin.start_span.return_value = child

    payload = trace_prepopulation_actions._seed_trace(admin, "exp", 0, "initial")

    admin.end_span.assert_called_once()
    admin.end_trace.assert_called_once()
    assert payload["spans"] == [root, child]


@pytest.mark.parametrize("ingestion_fails", [False, True])
def test_seed_traces_ingests_and_waits_for_sql_spans(
    monkeypatch: pytest.MonkeyPatch, ingestion_fails: bool,
) -> None:
    spans = [object(), object()]
    payload = {
        "trace_id": "trace-a", "root_span_id": "root",
        "child_span_id": "child", "spans": spans,
    }
    events = []
    monkeypatch.setattr(
        trace_prepopulation_actions, "_seed_trace", lambda *args: dict(payload)
    )
    monkeypatch.setattr(
        trace_prepopulation_actions, "_flush_async_trace_logging",
        lambda: events.append("flush"),
    )
    ingest = Mock(side_effect=(
        RuntimeError("OTLP failed") if ingestion_fails
        else lambda *args: events.append("ingest")
    ))
    monkeypatch.setattr(trace_prepopulation_actions, "_log_spans_via_otlp", ingest)
    monkeypatch.setattr(
        trace_prepopulation_actions, "_wait_for_traces_searchable",
        lambda *args: events.append("metadata"),
    )
    wait = Mock(side_effect=lambda *args: events.append("sql-spans"))
    monkeypatch.setattr(trace_prepopulation_actions, "_wait_for_sql_spans", wait)
    context = SimpleNamespace(
        user_client=Mock(), active_experiment_id="exp", active_workspace="workspace"
    )
    state = {}

    if ingestion_fails:
        with pytest.raises(RuntimeError, match="OTLP failed"):
            trace_prepopulation_actions._seed_traces(context, state, 1, "initial")
        assert events == ["flush"]
        wait.assert_not_called()
        return
    result = trace_prepopulation_actions._seed_traces(context, state, 1, "initial")

    assert events == ["flush", "ingest", "metadata", "sql-spans"]
    ingest.assert_called_once_with("exp", "workspace", spans)
    assert "spans" not in result[0]
    assert wait.call_args.args[1:] == (state, result)


@pytest.mark.parametrize("visible", [False, True])
def test_wait_for_sql_spans_requires_both_owned_span_ids(
    monkeypatch: pytest.MonkeyPatch, visible: bool
) -> None:
    query = Mock(side_effect=[
        "trace-a:root\ntrace-a:unrelated", "trace-a:root\ntrace-a:child",
    ])
    monkeypatch.setattr(trace_prepopulation_actions, "_database_query", query)
    monkeypatch.setattr(trace_prepopulation_actions.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        trace_prepopulation_actions.time, "monotonic",
        Mock(side_effect=[0, 0, 0 if visible else 61]),
    )
    payloads = [{
        "trace_id": "trace-a", "root_span_id": "root", "child_span_id": "child",
    }]
    if visible:
        trace_prepopulation_actions._wait_for_sql_spans(Mock(), _DATABASE_STATE, payloads)
        assert query.call_count == 2
    else:
        with pytest.raises(pytest.fail.Exception, match="did not become SQL-backed"):
            trace_prepopulation_actions._wait_for_sql_spans(Mock(), _DATABASE_STATE, payloads)
    sql = query.call_args.args[2]
    assert "trace-a" not in sql
    assert query.call_args.kwargs["params"] == {"v0": "trace-a"}


def test_prepopulation_suite_requires_explicit_marker_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An eligible harness environment must not bypass explicit marker selection."""
    mlflow_tests_dir = Path(trace_prepopulation_actions.__file__).resolve().parents[2]
    target = "tests/test_trace_analytics_prepopulation.py"
    monkeypatch.setenv("PYTEST_ADDOPTS", "-m smoke")

    def collect(mark_expression: str | None) -> str:
        command = [sys.executable, "-m", "pytest", "--collect-only", "-q", target]
        if mark_expression is not None:
            command += ["-m", mark_expression]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=mlflow_tests_dir,
            env={
                **os.environ,
                "PYTEST_ADDOPTS": "",
                "trace_analytics_prepopulation_enabled": "true",
            },
        )
        assert result.returncode in (0, 5), result.stderr
        return result.stdout

    assert "test_trace_analytics_prepopulation_job" not in collect(None)
    assert (
        "test_trace_analytics_prepopulation_job"
        in collect("trace_analytics_prepopulation")
    )


def test_built_job_follows_the_sample_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-run overrides must preserve the sample Job's hardening."""
    monkeypatch.setattr(trace_prepopulation_actions.Config, "TRACE_PREPOPULATION_IMAGE", "job-image:ci")
    state = {
        "secret_name": "resolved-backend-secret",
        "secret_key": "backend-store-uri",
        "batch_size": "2",
    }

    job = trace_prepopulation_actions._build_prepopulate_job(state, "prepop-job-1")

    pod_spec = job.spec.template.spec
    container = pod_spec.containers[0]
    assert job.metadata.name == "prepop-job-1"
    assert job.metadata.namespace == trace_prepopulation_actions._operator_namespace()
    assert job.metadata.labels["created-by"] == "mlflow-tests"
    # Sample hardening the hand-mirrored copy had drifted away from.
    assert job.spec.backoff_limit == 0
    assert job.spec.ttl_seconds_after_finished == 86400
    assert pod_spec.automount_service_account_token is False
    assert pod_spec.restart_policy == "Never"
    assert pod_spec.security_context.run_as_non_root is True
    assert container.security_context.allow_privilege_escalation is False
    assert container.security_context.capabilities.drop == ["ALL"]
    assert container.security_context.read_only_root_filesystem is True
    tmp_volume = next(volume for volume in pod_spec.volumes if volume.name == "tmp")
    assert tmp_volume.empty_dir is not None
    assert str(tmp_volume.empty_dir.size_limit) == "256Mi"
    tmp_mount = next(
        mount for mount in container.volume_mounts if mount.name == "tmp"
    )
    assert tmp_mount.mount_path == "/tmp"
    assert container.resources.limits == {"cpu": "1", "memory": "1Gi"}
    # Per-run overrides.
    assert container.image == "job-image:ci"
    assert container.args[-2:] == ["--batch-size", "2"]
    assert container.command == ["mlflow"]
    env_by_name = {entry.name: entry for entry in container.env}
    assert (
        env_by_name["MLFLOW_TRACKING_URI"].value_from.secret_key_ref.name
        == "resolved-backend-secret"
    )
    assert (
        env_by_name["MLFLOW_TRACKING_URI"].value_from.secret_key_ref.key
        == "backend-store-uri"
    )
    assert env_by_name["MLFLOW_READ_REPLICA_BACKEND_STORE_URI"].value == ""


def _secret_env(name: str, key: str = "backend-store-uri"):
    from kubernetes import client as k8s_client

    return k8s_client.V1EnvVar(
        name="PLACEHOLDER",
        value_from=k8s_client.V1EnvVarSource(
            secret_key_ref=k8s_client.V1SecretKeySelector(name=name, key=key)
        ),
    )


def _backend_secret_env(name: str, key: str = "backend-store-uri"):
    env = _secret_env(name, key)
    env.name = "MLFLOW_BACKEND_STORE_URI"
    return env


def _read_replica_secret_env(name: str, key: str = "backend-store-uri"):
    env = _secret_env(name, key)
    env.name = "MLFLOW_READ_REPLICA_BACKEND_STORE_URI"
    return env


def _deployment_with_env(env: list):
    from kubernetes import client as k8s_client

    container = k8s_client.V1Container(name="mlflow", image="mlflow:ci", env=env)
    template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels={}),
        spec=k8s_client.V1PodSpec(containers=[container]),
    )
    return k8s_client.V1Deployment(
        metadata=k8s_client.V1ObjectMeta(name="mlflow"),
        spec=k8s_client.V1DeploymentSpec(
            selector=k8s_client.V1LabelSelector(match_labels={}),
            template=template,
        ),
    )


def _resolve_effective_backend_secret(
    monkeypatch: pytest.MonkeyPatch, env: list, deployment=None,
):
    from kubernetes import client as k8s_client

    apps_api = Mock()
    apps_api.read_namespaced_deployment.return_value = (
        deployment if deployment is not None else _deployment_with_env(env)
    )
    core_api = Mock()
    core_api.read_namespaced_secret.return_value = Mock(data=json.dumps({
        "data": {
            key: base64.b64encode(
                b"postgresql://user:pass@host:5432/mydatabase"
            ).decode()
            for key in ("backend-store-uri", "custom-primary-uri")
        }
    }).encode())
    return trace_prepopulation_actions._effective_backend_secret(core_api, apps_api)


def test_prepopulation_job_inherits_postgres_ca_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_path = "/etc/mlflow/certs/combined.pem"
    deployment = _deployment_with_env([
        _backend_secret_env("primary"),
        k8s_client.V1EnvVar(name="PGSSLROOTCERT", value=ca_path),
        k8s_client.V1EnvVar(name="PGSSLMODE", value="verify-full"),
        k8s_client.V1EnvVar(name="UNRELATED", value="do-not-copy"),
    ])
    pod = deployment.spec.template.spec
    pod.volumes = [
        k8s_client.V1Volume(name="tmp", empty_dir=k8s_client.V1EmptyDirVolumeSource()),
        k8s_client.V1Volume(name="combined-ca-bundle", empty_dir=k8s_client.V1EmptyDirVolumeSource()),
        k8s_client.V1Volume(name="ca-bundle-0", config_map=k8s_client.V1ConfigMapVolumeSource(name="postgres-ca")),
        k8s_client.V1Volume(name="unrelated", secret=k8s_client.V1SecretVolumeSource(secret_name="unrelated")),
    ]
    pod.init_containers = [k8s_client.V1Container(
        name="combine-ca-bundles", image="predecessor:ci",
        command=["/bin/sh", "-c", "combine_ca_bundles"],
        volume_mounts=[
            k8s_client.V1VolumeMount(name="tmp", mount_path="/tmp"),
            k8s_client.V1VolumeMount(name="combined-ca-bundle", mount_path="/etc/mlflow/certs"),
            k8s_client.V1VolumeMount(name="ca-bundle-0", mount_path="/etc/postgres-ca", read_only=True),
        ],
        env=[k8s_client.V1EnvVar(name="CA_BUNDLE_OUTPUT", value=ca_path)],
    )]
    pod.containers[0].volume_mounts = [
        k8s_client.V1VolumeMount(name="combined-ca-bundle", mount_path="/etc/mlflow/certs", read_only=True),
        k8s_client.V1VolumeMount(name="unrelated", mount_path="/unrelated"),
    ]
    state = _resolve_effective_backend_secret(monkeypatch, [], deployment=deployment)
    monkeypatch.setattr(trace_prepopulation_actions.Config, "TRACE_PREPOPULATION_IMAGE", "job:ci")
    job = trace_prepopulation_actions._build_prepopulate_job(
        {**state, "batch_size": "2"}, "tls-prepopulation"
    )
    actual = job.spec.template.spec
    container = actual.containers[0]
    env = {entry.name: entry.value for entry in container.env}
    assert env["PGSSLROOTCERT"] == ca_path
    assert env["PGSSLMODE"] == "verify-full"
    assert "UNRELATED" not in env
    assert actual.init_containers == pod.init_containers
    assert {volume.name for volume in actual.volumes} == {
        "tmp", "combined-ca-bundle", "ca-bundle-0",
    }
    assert len([volume for volume in actual.volumes if volume.name == "tmp"]) == 1
    assert next(volume for volume in actual.volumes if volume.name == "ca-bundle-0").config_map.name == "postgres-ca"
    assert {mount.name for mount in container.volume_mounts} == {"tmp", "combined-ca-bundle"}
    assert next(mount for mount in container.volume_mounts if mount.name == "combined-ca-bundle").read_only
    assert container.security_context.read_only_root_filesystem


@pytest.mark.parametrize("body_type", ["secret", "log", "invalid-log", "invalid-secret"])
def test_sensitive_kubernetes_responses_are_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, body_type: str,
) -> None:
    sentinel = "SENSITIVE_RESPONSE_SENTINEL"
    encoded = base64.b64encode(f"postgresql://user:{sentinel}@host/db".encode()).decode()
    if body_type == "secret":
        body = json.dumps({"data": {"backend-store-uri": encoded}}).encode()
    elif body_type == "invalid-secret":
        body = (sentinel + "not-json").encode()
    elif body_type == "invalid-log":
        body = b"\xff" + sentinel.encode()
    else:
        body = sentinel.encode()
    response = HTTPResponse(body=BytesIO(body), status=200, preload_content=False)
    close = Mock(wraps=response.close)
    release = Mock(wraps=response.release_conn)
    monkeypatch.setattr(response, "close", close)
    monkeypatch.setattr(response, "release_conn", release)
    core = k8s_client.CoreV1Api()
    request = Mock(return_value=response)
    monkeypatch.setattr(core.api_client.rest_client.pool_manager, "request", request)
    caplog.set_level(logging.DEBUG, logger="kubernetes.client.rest")

    def read():
        if "secret" in body_type:
            apps = Mock()
            apps.read_namespaced_deployment.return_value = _deployment_with_env([
                _backend_secret_env("backend-secret"),
            ])
            return trace_prepopulation_actions._effective_backend_secret(core, apps)
        monkeypatch.setattr(core, "list_namespaced_pod", Mock(return_value=SimpleNamespace(
            items=[SimpleNamespace(metadata=SimpleNamespace(name="job-pod"))],
        )))
        return trace_prepopulation_actions._job_pod_logs(core, "job")

    if body_type.startswith("invalid"):
        with pytest.raises(pytest.fail.Exception) as error:
            read()
        assert sentinel not in str(error.value)
    elif body_type == "secret":
        assert read()["database"] == "db"
    else:
        assert read() == sentinel
    assert request.call_args.kwargs["preload_content"] is False
    assert sentinel not in caplog.text
    assert encoded not in caplog.text
    close.assert_called_once()
    assert release.called


@pytest.mark.parametrize(
    "replica_name,replica_key",
    [
        ("mlflow-read-replica", "replica-uri"),
        ("mlflow-backend-override", "replica-uri"),
        ("mlflow-backend-override", "custom-primary-uri"),
    ],
)
def test_job_uses_effective_backend_secret_regardless_of_replica_reference(
    monkeypatch: pytest.MonkeyPatch,
    replica_name: str,
    replica_key: str,
) -> None:
    resolved = _resolve_effective_backend_secret(
        monkeypatch,
        [
            _backend_secret_env("mlflow-backend-original"),
            _backend_secret_env("mlflow-backend-override", "custom-primary-uri"),
            _read_replica_secret_env(replica_name, replica_key),
        ],
    )

    assert resolved["secret_name"] == "mlflow-backend-override"
    assert resolved["secret_key"] == "custom-primary-uri"
    monkeypatch.setattr(
        trace_prepopulation_actions.Config, "TRACE_PREPOPULATION_IMAGE", "job-image:ci"
    )
    job = trace_prepopulation_actions._build_prepopulate_job(
        {**resolved, "batch_size": "2"}, "prepop-backend-selection"
    )
    env = {entry.name: entry for entry in job.spec.template.spec.containers[0].env}
    reference = env["MLFLOW_TRACKING_URI"].value_from.secret_key_ref
    assert (reference.name, reference.key) == (
        "mlflow-backend-override", "custom-primary-uri"
    )
    assert env["MLFLOW_READ_REPLICA_BACKEND_STORE_URI"].value == ""


def test_effective_backend_secret_does_not_retain_decoded_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared state must not retain credentials that pytest could render on failure."""
    resolved = _resolve_effective_backend_secret(
        monkeypatch, [_backend_secret_env("mlflow-backend")]
    )

    assert set(resolved) == {"secret_name", "secret_key", "database_user", "database"}


_VALID_JOB_OUTPUT = (
    "Traces progress: scanned=2, updated=2\n"
    "Spans progress: scanned=4, updated=4\n"
    "Traces: scanned=4, updated=4\n"
    "Spans: scanned=8, updated=8\n"
    "Assessments: scanned=0, updated=0\n"
    "Prepopulation completed without advancing the Alembic revision.\n"
)


def test_parse_prepopulation_output_extracts_counters() -> None:
    parsed = _parse_prepopulation_output(_VALID_JOB_OUTPUT)

    assert parsed["stats"]["Traces"] == {"scanned": 4, "updated": 4}
    assert parsed["stats"]["Spans"] == {"scanned": 8, "updated": 8}
    assert parsed["progress_entities"] == ["Spans", "Traces"]
    assert parsed["completion_seen"] is True


def test_parse_prepopulation_output_fails_on_missing_summary() -> None:
    with pytest.raises(pytest.fail.Exception, match="summary line"):
        _parse_prepopulation_output(
            "Traces progress: scanned=2, updated=2\n"
            "Prepopulation completed without advancing the Alembic revision.\n"
        )


def test_parse_prepopulation_output_fails_on_missing_completion() -> None:
    with pytest.raises(pytest.fail.Exception, match="completion line"):
        _parse_prepopulation_output(
            "Traces: scanned=4, updated=4\n"
            "Spans: scanned=8, updated=8\n"
            "Assessments: scanned=0, updated=0\n"
        )


def test_parser_failure_does_not_leak_raw_log_into_pytest_output(
    tmp_path: Path,
) -> None:
    """Check real pytest console/JUnit output for raw-log argument disclosure."""
    # Build the sentinel at runtime so the probe module's own source line and
    # traceback cannot contain the literal string.
    sentinel = "".join(["RAW_JOB_LOG_", "LEAK_SENTINEL_7f3a"])
    # parents[2] is the mlflow-tests project root, which contains the tests
    # package the probe imports.
    mlflow_tests_dir = Path(trace_prepopulation_actions.__file__).resolve().parents[2]
    probe = tmp_path / "test_leak_probe.py"
    # The sentinel is assembled at runtime inside the probe so neither its
    # source nor its traceback frames ever contain the full literal.
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(mlflow_tests_dir)!r})\n"
        "from tests.actions.trace_prepopulation_actions import "
        "_parse_prepopulation_output\n"
        "def test_probe():\n"
        "    _parse_prepopulation_output(''.join(['RAW_JOB_LOG_', "
        "'LEAK_SENTINEL_7f3a']))\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--junit-xml={junit}",
            str(probe),
        ],
        capture_output=True,
        text=True,
        cwd=mlflow_tests_dir,
    )

    assert result.returncode == 1, "probe test should fail through the parser"
    combined = result.stdout + result.stderr + junit.read_text(encoding="utf-8")
    assert sentinel not in combined
    assert sentinel not in junit.read_text(encoding="utf-8")


_INITIAL_PAYLOADS = [
    {
        "trace_id": f"initial-trace-{index}",
        "trace_name": f"initial-name-{index}",
        "root_span_id": f"root-{index}",
        "child_span_id": f"child-{index}",
    }
    for index in range(trace_prepopulation_actions.TRACE_COUNT_INITIAL)
]
_ADDITIONAL_PAYLOADS = [
    {
        "trace_id": f"additional-trace-{index}",
        "trace_name": f"additional-name-{index}",
        "root_span_id": f"additional-root-{index}",
        "child_span_id": f"additional-child-{index}",
    }
    for index in range(trace_prepopulation_actions.TRACE_COUNT_ADDITIONAL)
]


def _validation_state() -> dict:
    """Minimal prepopulation state for hermetic validation runs."""
    return {
        "cr_before": {
            "generation": 1,
            "status_version": "3.16.0",
            "deployment_image": "mlflow:predecessor",
            "deployment_replicas": 1,
            "migration_condition": {"status": "True", "observed_generation": 1},
            "force_migrate_pending": False,
        },
        "secret_name": "mlflow-db-credentials",
        "secret_key": "backend-store-uri",
        "revision_before": trace_prepopulation_actions.PREDECESSOR_SCHEMA_REVISION,
        "trace_info_columns_before": {"trace_name": False, "session_id": False},
        "spans_columns_before": {"model_name": False, "model_provider": False},
        "payloads": {
            "initial": [dict(payload) for payload in _INITIAL_PAYLOADS],
            "additional": [dict(payload) for payload in _ADDITIONAL_PAYLOADS],
        },
        "xmin_after_first_run": {
            payload["trace_id"]: 100 + index
            for index, payload in enumerate(_INITIAL_PAYLOADS)
        },
        "span_xmin_after_first_run": {
            f"{payload['trace_id']}/{payload[key]}": 200 + index
            for index, payload in enumerate(_INITIAL_PAYLOADS)
            for key in ("root_span_id", "child_span_id")
        },
        "parsed_output": {
            "first": {
                "stats": {
                    "Traces": {"scanned": 4, "updated": 4},
                    "Spans": {"scanned": 8, "updated": 8},
                    "Assessments": {"scanned": 0, "updated": 0},
                },
                "progress_entities": ["Traces"],
            },
            "second": {
                "stats": {
                    "Traces": {"scanned": 6, "updated": 2},
                    "Spans": {"scanned": 12, "updated": 4},
                    "Assessments": {"scanned": 0, "updated": 0},
                },
                "progress_entities": ["Traces"],
            },
        },
        "api_errors": {"first": [], "second": []},
        "assessment_id": "seeded-assessment",
        "assessment_xmin_after_first_run": "123",
        "migration_overlap": {"first": [], "second": []},
    }


def _patch_validation_dependencies(
    monkeypatch: pytest.MonkeyPatch, state: dict
) -> None:
    """Replace the validations' cluster-facing helpers with hermetic fakes."""

    def fake_database_query(core_api, state_arg, sql, params=None):
        if "FROM assessments" in sql:
            assert params == {
                "assessment_id": state_arg["assessment_id"],
                "trace_id": state_arg["payloads"]["initial"][0]["trace_id"],
            }
            return "123"
        if "json_object_agg(request_id, trace_name)" in sql:
            return json.dumps({
                payload["trace_id"]: payload["trace_name"]
                for batch in state_arg["payloads"].values()
                for payload in batch
                if payload["trace_id"] in params.values()
            })
        if "FROM spans" in sql:
            assert "model_name = :'model' AND model_provider = :'provider'" in sql
            assert params["model"] == trace_prepopulation_actions.TRACE_MODEL_NAME
            assert params["provider"] == trace_prepopulation_actions.TRACE_MODEL_PROVIDER
            return "\n".join(
                f"{payload['trace_id']}:{payload[key]}"
                for batch in state_arg["payloads"].values()
                for payload in batch
                if payload["trace_id"] in params.values()
                for key in ("root_span_id", "child_span_id")
            )
        raise AssertionError(f"unexpected query: {sql}")

    def fake_trace_row_states(core_api, state_arg, trace_ids):
        return {trace_id: 100 + index for index, trace_id in enumerate(trace_ids)}

    monkeypatch.setattr(trace_prepopulation_validations, "_core_api", lambda: Mock())
    monkeypatch.setattr(
        trace_prepopulation_validations, "_database_query", fake_database_query
    )
    monkeypatch.setattr(
        trace_prepopulation_validations,
        "_analytics_column_exists",
        lambda core_api, state_arg, table, column: True,
    )
    monkeypatch.setattr(
        trace_prepopulation_validations, "_trace_row_states", fake_trace_row_states
    )
    monkeypatch.setattr(
        trace_prepopulation_validations,
        "_span_row_states",
        lambda core_api, state_arg, payloads: dict(state["span_xmin_after_first_run"]),
    )
    monkeypatch.setattr(
        trace_prepopulation_validations,
        "_query_revision",
        lambda core_api, state_arg: state["revision_before"],
    )
    monkeypatch.setattr(
        trace_prepopulation_validations, "_cr_snapshot", lambda: state["cr_before"]
    )


@pytest.mark.parametrize("batch", ["initial", "additional"])
@pytest.mark.parametrize("fault", [None, "swapped", "wrong", "null", "missing"])
def test_trace_names_match_each_owned_id(
    monkeypatch: pytest.MonkeyPatch, batch: str, fault: str | None
) -> None:
    state = _validation_state()
    payloads = state["payloads"][batch]
    actual = {
        payload["trace_id"]: payload["trace_name"] for payload in reversed(payloads)
    }
    first, second = [payload["trace_id"] for payload in payloads[:2]]
    if fault == "swapped":
        actual[first], actual[second] = actual[second], actual[first]
    elif fault == "wrong":
        actual[first] = "wrong-name"
    elif fault == "null":
        actual[first] = None
    elif fault == "missing":
        del actual[first]
    query = Mock(return_value=json.dumps(actual))
    monkeypatch.setattr(trace_prepopulation_validations, "_database_query", query)

    if fault is None:
        trace_prepopulation_validations._assert_trace_names(Mock(), state, batch)
    else:
        with pytest.raises(pytest.fail.Exception, match=f"{batch} trace IDs and names"):
            trace_prepopulation_validations._assert_trace_names(Mock(), state, batch)
    assert set(query.call_args.kwargs["params"].values()) == {
        payload["trace_id"] for payload in payloads
    }
    assert first not in query.call_args.args[2]


@pytest.mark.parametrize("run", ["first", "second"])
def test_run_rejects_incomplete_span_model_backfill(
    monkeypatch: pytest.MonkeyPatch, run: str,
) -> None:
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)
    original_query = trace_prepopulation_validations._database_query

    def query_with_bad_owned_span(core_api, state_arg, sql, params=None):
        rows = original_query(core_api, state_arg, sql, params=params)
        if "FROM spans" in sql:
            # A missing/wrong-model owned span cannot be replaced by an
            # unrelated span with correct model fields.
            return "\n".join(rows.splitlines()[:-1] + ["unrelated:span"])
        return rows

    monkeypatch.setattr(
        trace_prepopulation_validations, "_database_query", query_with_bad_owned_span
    )
    with pytest.raises(pytest.fail.Exception, match="Seeded spans are missing"):
        getattr(trace_prepopulation_validations, f"validate_{run}_prepopulation_job")(
            SimpleNamespace(prepopulation_state=state)
        )


@pytest.mark.parametrize("xmin", ["", "124"])
def test_second_run_rejects_missing_or_rewritten_assessment(monkeypatch, xmin):
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)
    query = trace_prepopulation_validations._database_query

    def fake_query(core_api, state_arg, sql, params=None):
        if "FROM assessments" in sql:
            return xmin
        return query(core_api, state_arg, sql, params=params)

    monkeypatch.setattr(trace_prepopulation_validations, "_database_query", fake_query)
    with pytest.raises(pytest.fail.Exception, match="assessment"):
        trace_prepopulation_validations.validate_second_prepopulation_job(
            SimpleNamespace(prepopulation_state=state)
        )


def test_second_run_rejects_rewritten_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)
    after = dict(state["span_xmin_after_first_run"])
    after[next(iter(after))] += 1
    monkeypatch.setattr(
        trace_prepopulation_validations, "_span_row_states", Mock(return_value=after)
    )
    with pytest.raises(pytest.fail.Exception, match="span rows changed"):
        trace_prepopulation_validations.validate_second_prepopulation_job(
            SimpleNamespace(prepopulation_state=state)
        )


def test_first_run_captures_span_row_states(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)
    expected = dict(state.pop("span_xmin_after_first_run"))
    capture = Mock(return_value=expected)
    monkeypatch.setattr(trace_prepopulation_validations, "_span_row_states", capture)
    trace_prepopulation_validations.validate_first_prepopulation_job(
        SimpleNamespace(prepopulation_state=state)
    )
    assert state["span_xmin_after_first_run"] == expected
    assert capture.call_args.args[2] == state["payloads"]["initial"]


def test_first_run_validation_tolerates_unrelated_row_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter bounds use owned rows because concurrent writers may delete others."""
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)

    trace_prepopulation_validations.validate_first_prepopulation_job(
        SimpleNamespace(prepopulation_state=state)
    )

    state["parsed_output"]["first"]["stats"]["Traces"] = {"scanned": 4, "updated": 3}
    with pytest.raises(pytest.fail.Exception, match="seeded rows required backfill"):
        trace_prepopulation_validations.validate_first_prepopulation_job(
            SimpleNamespace(prepopulation_state=state)
        )


def test_second_run_validation_tolerates_unrelated_row_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rerun bounds must catch missing owned rows without assuming global counts."""
    state = _validation_state()
    _patch_validation_dependencies(monkeypatch, state)

    trace_prepopulation_validations.validate_second_prepopulation_job(
        SimpleNamespace(prepopulation_state=state)
    )

    state["parsed_output"]["second"]["stats"]["Traces"] = {"scanned": 5, "updated": 2}
    with pytest.raises(pytest.fail.Exception, match="seeded by the test"):
        trace_prepopulation_validations.validate_second_prepopulation_job(
            SimpleNamespace(prepopulation_state=state)
        )
    state["parsed_output"]["second"]["stats"]["Traces"] = {"scanned": 6, "updated": 1}
    with pytest.raises(
        pytest.fail.Exception, match="newly written rows were not corrected"
    ):
        trace_prepopulation_validations.validate_second_prepopulation_job(
            SimpleNamespace(prepopulation_state=state)
        )
