"""Run prepopulation Jobs against a live predecessor-schema PostgreSQL deployment.

Keep database credentials out of shared state and raw Job logs out of reports;
retain only Secret references, parsed counters, and lifecycle diagnostics.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
import yaml
import mlflow
from kubernetes import client
from kubernetes.client.rest import ApiException

from mlflow_tests.utils.client import ClientManager

from ..constants.config import Config
from ..shared import TestContext
from .trace_archival_actions import _log_spans_via_otlp

logger = logging.getLogger(__name__)

PREPOPULATION_JOB_PREFIX = "mlflow-trace-analytics-prepopulation"
PREPOPULATION_CONTAINER_NAME = "mlflow-prepopulate-trace-analytics"
# Small batches force multiple committed transactions per entity so progress
# reporting and rerun skipping are both observable on a tiny dataset. The
# command and remaining args come from the sample manifest.
PREPOPULATION_BATCH_SIZE = "2"

MLFLOW_DEPLOYMENT_NAME = "mlflow"
MLFLOW_DEPLOYMENT_CONTAINER = "mlflow"
BACKEND_STORE_ENV_NAME = "MLFLOW_BACKEND_STORE_URI"
TRACKING_URI_ENV_NAME = "MLFLOW_TRACKING_URI"
# The test builds its Job from the checked-in sample so the live coverage
# exercises the artifact administrators apply; the test image ships the repo
# (mlflow-tests/images/Dockerfile.konflux copies ./config), keeping this
# path valid in-cluster.
SAMPLE_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "samples"
    / "trace_analytics_prepopulation_job.yaml"
)
MLFLOW_API_GROUP = "mlflow.opendatahub.io"
MLFLOW_API_VERSION = "v1"
MLFLOW_API_PLURAL = "mlflows"
MLFLOW_CR_NAME = "mlflow"
MIGRATION_JOB_LABEL = "mlflow.opendatahub.io/migration-job=true"
MIGRATION_FORCE_ANNOTATION = "mlflow.opendatahub.io/force-migrate"

# The prepopulation command refuses any other revision; the predecessor
# deployment must serve at exactly this schema.
PREDECESSOR_SCHEMA_REVISION = "b7e2c1a4d9f3"

POSTGRES_POD_LABEL = "app=mlflow-postgres"
DATABASE_POD_TIMEOUT_SECONDS = 120
DATABASE_QUERY_TIMEOUT_SECONDS = 60
JOB_COMPLETE_TIMEOUT_SECONDS = 300
TRACE_VISIBILITY_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 2

TRACE_COUNT_INITIAL = 4
TRACE_COUNT_ADDITIONAL = 2
TRACE_MODEL_NAME = "prepopulation-smoke-model"
TRACE_MODEL_PROVIDER = "prepopulation-smoke-provider"

SUMMARY_LINE_RE = re.compile(
    r"^(Traces|Spans|Assessments): scanned=(\d+), updated=(\d+)$"
)
PROGRESS_LINE_RE = re.compile(
    r"^(Traces|Spans|Assessments) progress: scanned=(\d+), updated=(\d+)$"
)
COMPLETION_LINE = (
    "Prepopulation completed without advancing the Alembic revision."
)


def _operator_namespace() -> str:
    return Config.MLFLOW_NAMESPACE


def _mlflow_cr(custom_api: client.CustomObjectsApi) -> dict[str, Any]:
    return custom_api.get_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name=MLFLOW_CR_NAME,
    )


def _cr_snapshot() -> dict[str, Any]:
    """Capture the MLflow CR identity fields the exercise must not disturb."""
    custom_api = client.CustomObjectsApi()
    mlflow_cr = _mlflow_cr(custom_api)
    apps_api = client.AppsV1Api()
    deployment = apps_api.read_namespaced_deployment(
        MLFLOW_DEPLOYMENT_NAME, _operator_namespace()
    )
    main_container = next(
        (
            container
            for container in deployment.spec.template.spec.containers
            if container.name == MLFLOW_DEPLOYMENT_CONTAINER
        ),
        None,
    )
    if main_container is None:
        pytest.fail(
            f"Deployment {MLFLOW_DEPLOYMENT_NAME} in namespace "
            f"{_operator_namespace()} has no {MLFLOW_DEPLOYMENT_CONTAINER} container"
        )
    migration_condition = next(
        (
            condition
            for condition in mlflow_cr.get("status", {}).get("conditions", [])
            if condition.get("type") == "Migration"
        ),
        None,
    )
    return {
        "generation": mlflow_cr["metadata"]["generation"],
        "status_version": mlflow_cr.get("status", {}).get("version", ""),
        "deployment_image": main_container.image,
        "deployment_replicas": deployment.spec.replicas,
        # Compared by _assert_cr_unchanged, so capture only stable fields:
        # a Migration condition flip mid-exercise is exactly what must fail.
        "migration_condition": None
        if migration_condition is None
        else {
            "status": migration_condition.get("status"),
            "observed_generation": migration_condition.get("observedGeneration"),
        },
        "force_migrate_pending": MIGRATION_FORCE_ANNOTATION
        in mlflow_cr["metadata"].get("annotations", {}),
    }


def _effective_backend_secret(
    core_api: client.CoreV1Api, apps_api: client.AppsV1Api
) -> dict[str, Any]:
    """Resolve the primary backend Secret the live Deployment actually uses.

    Later explicit env entries override generated wiring. Return only Secret
    references, non-secret connection fields, and CA configuration, not the URI.
    """
    deployment = apps_api.read_namespaced_deployment(
        MLFLOW_DEPLOYMENT_NAME, _operator_namespace()
    )
    main_container = next(
        (
            container
            for container in deployment.spec.template.spec.containers
            if container.name == MLFLOW_DEPLOYMENT_CONTAINER
        ),
        None,
    )
    if main_container is None:
        pytest.fail(
            f"Deployment {MLFLOW_DEPLOYMENT_NAME} in namespace "
            f"{_operator_namespace()} has no {MLFLOW_DEPLOYMENT_CONTAINER} container"
        )

    secret_name: str | None = None
    secret_key: str | None = None
    for env_var in reversed(main_container.env or []):
        if env_var.name == BACKEND_STORE_ENV_NAME:
            if env_var.value_from is None or env_var.value_from.secret_key_ref is None:
                pytest.fail(
                    "Effective MLFLOW_BACKEND_STORE_URI on the live Deployment is not "
                    "Secret-backed; the prepopulation workflow requires a Secret-backed "
                    "PostgreSQL backend store"
                )
            secret_name = env_var.value_from.secret_key_ref.name
            secret_key = env_var.value_from.secret_key_ref.key
            break

    if secret_name is None or secret_key is None:
        pytest.fail(
            f"Deployment {MLFLOW_DEPLOYMENT_NAME} does not set "
            f"{BACKEND_STORE_ENV_NAME}; cannot resolve the primary backend Secret"
        )
    # Preloaded responses are logged at DEBUG by the Kubernetes client.
    try:
        response = core_api.read_namespaced_secret(
            secret_name, _operator_namespace(), _preload_content=False
        )
        try:
            secret_data = json.loads(response.data).get("data") or {}
        finally:
            response.close()
            response.release_conn()
    except (ApiException, ValueError, UnicodeError):
        pytest.fail("Could not read the primary backend Secret", pytrace=False)
    uri = secret_data.get(secret_key)
    if not uri:
        pytest.fail(
            f"Secret {secret_name}/{_operator_namespace()} key {secret_key!r} is empty"
        )
    backend_uri = base64.b64decode(uri).decode("utf-8")
    parsed = urlparse(backend_uri)
    if parsed.scheme.split("+", 1)[0] != "postgresql":
        pytest.fail(
            "Resolved backend store URI is not PostgreSQL; the live prepopulation "
            "workflow is only supported on PostgreSQL-backed deployments"
        )
    database_user = unquote(parsed.username or "")
    database = (parsed.path or "").lstrip("/")
    if not database_user or not database:
        pytest.fail("Resolved backend store URI lacks a user or database name")
    resolved = {
        "secret_name": secret_name,
        "secret_key": secret_key,
        "database_user": database_user,
        "database": database,
    }
    pod_spec = deployment.spec.template.spec
    ca_init = next(
        (container for container in pod_spec.init_containers or []
         if container.name == "combine-ca-bundles"),
        None,
    )
    if ca_init is not None:
        # Reuse the operator-rendered CA setup, not unrelated server volumes
        # or environment variables. The sample already provides /tmp.
        ca_volumes = {
            mount.name for mount in ca_init.volume_mounts or [] if mount.name != "tmp"
        }
        serialize = client.ApiClient().sanitize_for_serialization
        resolved["ca_bundle"] = {
            "initContainers": [serialize(ca_init)],
            "volumes": [serialize(volume) for volume in pod_spec.volumes or []
                        if volume.name in ca_volumes],
            "volumeMounts": [serialize(mount) for mount in main_container.volume_mounts or []
                             if mount.name in ca_volumes],
            "env": [serialize(env) for env in main_container.env or []
                    if env.name in {"PGSSLROOTCERT", "PGSSLMODE"}],
        }
    return resolved


def _wait_for_database_pod(core_api: client.CoreV1Api) -> str:
    deadline = time.monotonic() + DATABASE_POD_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            pods = core_api.list_namespaced_pod(
                namespace=_operator_namespace(), label_selector=POSTGRES_POD_LABEL
            )
            for pod in pods.items:
                if pod.status.phase == "Running" and pod.metadata.name:
                    return pod.metadata.name
        except ApiException as exc:
            last_error = exc
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"No running PostgreSQL pod with label {POSTGRES_POD_LABEL!r} in namespace "
        f"{_operator_namespace()} within {DATABASE_POD_TIMEOUT_SECONDS}s: {last_error}"
    )


def _sql_in_bindings(values: list[str]) -> tuple[str, dict[str, str]]:
    """Build IN-list placeholders and their psql quoted-literal variables."""
    placeholders = ", ".join(f":'v{index}'" for index in range(len(values)))
    params = {f"v{index}": value for index, value in enumerate(values)}
    return placeholders, params


def _database_query(
    _core_api: client.CoreV1Api,
    state: dict[str, Any],
    sql: str,
    params: dict[str, str] | None = None,
) -> str:
    """Run one read-only SQL statement through psql in the database pod.

    Reference ``params`` as ``:'name'`` for psql literal quoting (not server-side
    binding). SQL uses stdin because ``-c`` does not expand psql variables.
    kubectl uses the harness kubeconfig; the timeout covers setup and execution.
    """
    __tracebackhide__ = True
    command = [
        "kubectl",
        "exec",
        "-i",
        "-n",
        _operator_namespace(),
        state["database_pod"],
        "--",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        # The -t -A output shape is load-bearing for the parsers below;
        # never let a start-up file reconfigure it.
        "-X",
    ]
    for name, value in (params or {}).items():
        command += ["-v", f"{name}={value}"]
    command += [
        "-U",
        state["database_user"],
        "-d",
        state["database"],
        "-t",
        "-A",
    ]
    # Capture both streams: kubectl/psql diagnostics and timeout exceptions
    # can contain query text or row data and must never become test evidence.
    try:
        result = subprocess.run(
            command,
            input=sql + "\n\\q\n",
            capture_output=True,
            text=True,
            timeout=DATABASE_QUERY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        error_message = "Database query timed out during connection setup or execution"
    except (OSError, UnicodeError):
        error_message = "Could not execute the database query"
    else:
        if result.returncode == 0:
            return result.stdout.strip()
        error_message = (
            f"Database query failed in pod {state['database_pod']} "
            f"(kubectl/psql exit {result.returncode})"
        )
    # Fail outside the handler so pytest cannot render a chained exception
    # containing the command or partially captured output.
    pytest.fail(error_message, pytrace=False)


def _query_revision(core_api: client.CoreV1Api, state: dict[str, Any]) -> str:
    return _database_query(
        core_api, state, "SELECT version_num FROM alembic_version"
    )


def _analytics_column_exists(
    core_api: client.CoreV1Api, state: dict[str, Any], table: str, column: str
) -> bool:
    result = _database_query(
        core_api,
        state,
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name = :'v0' AND column_name = :'v1'",
        params={"v0": table, "v1": column},
    )
    return result == "1"


def _parse_trace_row_states(rows: str, trace_ids: list[str]) -> dict[str, int]:
    """Parse row-ID:xmin evidence, requiring each expected ID exactly once.

    Incomplete maps must not compare equal and falsely prove idempotency.
    """
    states: dict[str, str] = {}
    for row in rows.splitlines():
        request_id, separator, xmin = row.partition(":")
        if not separator:
            continue
        if request_id in states:
            pytest.fail(
                f"Database row-state evidence has duplicate rows for trace "
                f"{request_id!r}"
            )
        states[request_id] = xmin
    expected = set(trace_ids)
    missing = sorted(expected - states.keys())
    if missing:
        pytest.fail(
            f"Database row-state evidence is incomplete: {len(missing)} of "
            f"{len(expected)} expected trace rows were not returned"
        )
    unexpected = sorted(states.keys() - expected)
    if unexpected:
        pytest.fail(
            f"Database row-state evidence returned {len(unexpected)} "
            "unexpected rows"
        )
    non_numeric = sorted(
        trace_id for trace_id, xmin in states.items() if not xmin.isdigit()
    )
    if non_numeric:
        pytest.fail(
            f"Database row-state evidence has a non-numeric xmin for "
            f"{len(non_numeric)} trace rows"
        )
    return {trace_id: int(states[trace_id]) for trace_id in trace_ids}


def _trace_row_states(
    core_api: client.CoreV1Api,
    state: dict[str, Any],
    trace_ids: list[str],
) -> dict[str, int]:
    """Return trace xmin values; unchanged xmin proves no row rewrite on rerun."""
    if not trace_ids:
        return {}
    placeholders, params = _sql_in_bindings(trace_ids)
    rows = _database_query(
        core_api,
        state,
        "SELECT request_id || ':' || xmin::text FROM trace_info "
        "WHERE request_id IN (" + placeholders + ")",
        params=params,
    )
    return _parse_trace_row_states(rows, trace_ids)


def _span_row_states(
    core_api: client.CoreV1Api,
    state: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> dict[str, int]:
    """Return complete owned trace/span -> xmin evidence for rerun checks."""
    if not payloads:
        return {}
    placeholders, params = _sql_in_bindings([p["trace_id"] for p in payloads])
    rows = _database_query(
        core_api,
        state,
        "SELECT trace_id || '/' || span_id || ':' || xmin::text FROM spans "
        "WHERE trace_id IN (" + placeholders + ")",
        params=params,
    )
    expected = [
        f"{payload['trace_id']}/{payload[key]}"
        for payload in payloads
        for key in ("root_span_id", "child_span_id")
    ]
    return _parse_trace_row_states(rows, expected)


def _seed_trace(
    admin_client: Any, experiment_id: str, index: int, batch: str
) -> dict[str, Any]:
    """Seed a trace name and two spans with model attributes to backfill."""
    trace_name = f"prepopulation-{batch}-{index}"
    root_span = admin_client.start_trace(
        name=trace_name,
        attributes={
            "mlflow.llm.model": TRACE_MODEL_NAME,
            "mlflow.llm.provider": TRACE_MODEL_PROVIDER,
        },
        experiment_id=experiment_id,
    )
    trace_id = getattr(root_span, "request_id", None) or getattr(
        root_span, "trace_id", None
    )
    if trace_id is None:
        raise AssertionError(f"Trace '{trace_name}' did not return a trace/request id")
    child_span = admin_client.start_span(
        name=f"{trace_name}-child",
        trace_id=trace_id,
        parent_id=root_span.span_id,
        attributes={
            "mlflow.llm.model": TRACE_MODEL_NAME,
            "mlflow.llm.provider": TRACE_MODEL_PROVIDER,
        },
    )
    admin_client.end_span(
        trace_id=trace_id,
        span_id=child_span.span_id,
        status="OK",
    )
    admin_client.end_trace(trace_id=trace_id, status="OK")
    return {
        "trace_id": trace_id,
        "trace_name": trace_name,
        "root_span_id": root_span.span_id,
        "child_span_id": child_span.span_id,
        "spans": [root_span, child_span],
    }


def _flush_async_trace_logging() -> None:
    flush = getattr(mlflow, "flush_trace_async_logging", None)
    if callable(flush):
        flush()


def _wait_for_traces_searchable(
    admin_client: Any, experiment_id: str, trace_ids: list[str]
) -> None:
    expected = set(trace_ids)
    deadline = time.monotonic() + TRACE_VISIBILITY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        traces = admin_client.search_traces(
            experiment_ids=[experiment_id], max_results=100, include_spans=False
        )
        visible = {trace.info.trace_id for trace in traces}
        if expected <= visible:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    missing = sorted(expected - visible)
    pytest.fail(
        f"Seeded traces did not become searchable within "
        f"{TRACE_VISIBILITY_TIMEOUT_SECONDS}s: {len(missing)} missing"
    )


def _wait_for_sql_spans(
    core_api: client.CoreV1Api,
    state: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> None:
    expected = {
        f"{payload['trace_id']}:{payload[key]}"
        for payload in payloads
        for key in ("root_span_id", "child_span_id")
    }
    placeholders, params = _sql_in_bindings(
        [payload["trace_id"] for payload in payloads]
    )
    deadline = time.monotonic() + TRACE_VISIBILITY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        rows = _database_query(
            core_api,
            state,
            "SELECT trace_id || ':' || span_id FROM spans WHERE trace_id IN ("
            + placeholders
            + ")",
            params=params,
        )
        if expected <= set(rows.splitlines()):
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"Seeded spans did not become SQL-backed within "
        f"{TRACE_VISIBILITY_TIMEOUT_SECONDS}s"
    )


def _seed_traces(
    test_context: TestContext, state: dict[str, Any], count: int, batch: str
) -> list[dict[str, Any]]:
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before seeding traces")
    if not test_context.active_experiment_id:
        raise ValueError(
            "test_context.active_experiment_id must be set before seeding traces"
        )
    if not test_context.active_workspace:
        raise ValueError(
            "test_context.active_workspace must be set before seeding traces"
        )
    payloads = [
        _seed_trace(
            test_context.user_client,
            test_context.active_experiment_id,
            index,
            batch,
        )
        for index in range(count)
    ]
    _flush_async_trace_logging()
    # Kind port-forwarding bypasses the Gateway's /mlflow/v1 rewrite;
    # explicitly ingest via the shared prefixed/unprefixed OTLP fallback.
    for payload in payloads:
        _log_spans_via_otlp(
            test_context.active_experiment_id,
            test_context.active_workspace,
            payload.pop("spans"),
        )
    _wait_for_traces_searchable(
        test_context.user_client,
        test_context.active_experiment_id,
        [payload["trace_id"] for payload in payloads],
    )
    _wait_for_sql_spans(client.CoreV1Api(), state, payloads)
    return payloads


def _build_prepopulate_job(
    state: dict[str, Any], job_name: str
) -> client.V1Job:
    """Adapt the sample Job with per-run settings and the live backend's CA trust."""
    with SAMPLE_MANIFEST_PATH.open(encoding="utf-8") as manifest:
        sample = yaml.safe_load(manifest)
    if not isinstance(sample, dict) or sample.get("kind") != "Job":
        pytest.fail(
            f"Sample prepopulation Job manifest at {SAMPLE_MANIFEST_PATH} is "
            "missing or not a Job"
        )

    metadata = sample.setdefault("metadata", {})
    metadata["name"] = job_name
    metadata["namespace"] = _operator_namespace()
    metadata.setdefault("labels", {})["created-by"] = "mlflow-tests"

    pod_spec = sample["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    container["image"] = Config.TRACE_PREPOPULATION_IMAGE
    container["image_pull_policy"] = "IfNotPresent"
    env_by_name = {entry["name"]: entry for entry in container.setdefault("env", [])}
    secret_ref = env_by_name[TRACKING_URI_ENV_NAME]["valueFrom"]["secretKeyRef"]
    secret_ref["name"] = state["secret_name"]
    secret_ref["key"] = state["secret_key"]
    if ca_bundle := state.get("ca_bundle"):
        pod_spec["initContainers"] = ca_bundle["initContainers"]
        pod_spec["volumes"].extend(ca_bundle["volumes"])
        container["volumeMounts"].extend(ca_bundle["volumeMounts"])
        container["env"].extend(ca_bundle["env"])
    args = container["args"]
    try:
        batch_size_index = args.index("--batch-size")
    except ValueError:
        pytest.fail("Sample prepopulation Job args do not carry --batch-size")
    args[batch_size_index + 1] = state["batch_size"]

    return client.ApiClient().deserialize(
        _JsonObjectPayload(json.dumps(sample)), "V1Job"
    )


class _JsonObjectPayload:
    """Minimal response shim for ApiClient.deserialize (needs only .data)."""

    def __init__(self, data: str) -> None:
        self.data = data


def _parse_prepopulation_output(log_text: str) -> dict[str, Any]:
    """Parse the CLI's fixed output format into counters.

    Hide this frame because pytest can render raw log arguments into reports.
    Return only validated counters and fixed status fields.
    """
    __tracebackhide__ = True
    stats: dict[str, dict[str, int]] = {}
    progress_entities: set[str] = set()
    completion_seen = False
    for line in log_text.splitlines():
        stripped = line.strip()
        progress_match = PROGRESS_LINE_RE.match(stripped)
        if progress_match is not None:
            progress_entities.add(progress_match.group(1))
            continue
        summary_match = SUMMARY_LINE_RE.match(stripped)
        if summary_match is not None:
            stats[summary_match.group(1)] = {
                "scanned": int(summary_match.group(2)),
                "updated": int(summary_match.group(3)),
            }
            continue
        if COMPLETION_LINE in stripped:
            completion_seen = True

    for entity in ("Traces", "Spans", "Assessments"):
        if entity not in stats:
            pytest.fail(
                f"Prepopulation Job output did not contain the expected "
                f"'{entity}: scanned=..., updated=...' summary line"
            )
    if not completion_seen:
        pytest.fail(
            "Prepopulation Job output did not contain the completion line"
        )
    return {
        "stats": stats,
        "progress_entities": sorted(progress_entities),
        "completion_seen": completion_seen,
    }


def _job_pod_status_diagnostics(
    core_api: client.CoreV1Api, job_name: str
) -> str:
    """Lifecycle-only pod diagnostics; deliberately excludes container logs."""
    namespace = _operator_namespace()
    lines = [f"Job {job_name} pod status in namespace {namespace}:"]
    try:
        pods = core_api.list_namespaced_pod(
            namespace, label_selector=f"job-name={job_name}"
        )
    except ApiException as exc:
        return f"{lines[0]} failed to list pods: {exc.reason}"
    if not pods.items:
        lines.append("No pods found for job-name selector.")
    for pod in pods.items:
        phase = pod.status.phase if pod.status else "unknown"
        lines.append(f"Pod {pod.metadata.name} phase={phase}")
        for status in (pod.status.container_statuses or []) if pod.status else []:
            waiting = status.state.waiting if status.state else None
            terminated = status.state.terminated if status.state else None
            if waiting is not None:
                lines.append(
                    f"  container {status.name} waiting reason={waiting.reason}"
                )
            if terminated is not None:
                lines.append(
                    f"  container {status.name} terminated reason={terminated.reason} "
                    f"exit_code={terminated.exit_code}"
                )
        try:
            events = core_api.list_namespaced_event(
                namespace,
                field_selector=f"involvedObject.name={pod.metadata.name}",
            )
            for event in events.items:
                lines.append(f"  event type={event.type} reason={event.reason}")
        except Exception:
            lines.append("  events unavailable")
    return "\n".join(lines)


def _job_condition(job: client.V1Job, condition_type: str) -> str | None:
    for condition in (job.status.conditions if job.status else None) or []:
        if condition.type == condition_type:
            return condition.status
    return None


def _migration_jobs_since(
    batch_api: client.BatchV1Api, started: str
) -> list[str]:
    jobs = batch_api.list_namespaced_job(
        namespace=_operator_namespace(), label_selector=MIGRATION_JOB_LABEL
    ).items
    return [
        job.metadata.name
        for job in jobs
        if job.metadata.creation_timestamp
        and job.metadata.creation_timestamp.strftime("%Y-%m-%dT%H:%M:%S")
        >= started
    ]


def _api_probe(test_context: TestContext) -> str | None:
    """Light authenticated API call; returns an error class name or None."""
    try:
        test_context.user_client.search_experiments(max_results=1)
        return None
    except Exception as exc:  # noqa: BLE001 - recorded as a class name only
        return type(exc).__name__


def _run_prepopulate_job(
    test_context: TestContext, state: dict[str, Any], run_key: str
) -> None:
    ClientManager.load_k8s_config()
    batch_api = client.BatchV1Api()
    core_api = client.CoreV1Api()

    job_name = f"{PREPOPULATION_JOB_PREFIX}-{run_key}-{uuid.uuid4().hex[:8]}"
    batch_api.create_namespaced_job(
        _operator_namespace(), _build_prepopulate_job(state, job_name)
    )
    test_context.add_job_for_cleanup(job_name, _operator_namespace())
    state.setdefault("jobs", {})[run_key] = job_name

    api_errors: list[str] = []
    deadline = time.monotonic() + JOB_COMPLETE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            job = batch_api.read_namespaced_job_status(
                job_name, _operator_namespace()
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if _job_condition(job, "Failed") == "True":
            state.setdefault("api_errors", {})[run_key] = api_errors
            pytest.fail(
                f"Prepopulation Job {job_name} Failed before completion.\n"
                f"{_job_pod_status_diagnostics(core_api, job_name)}"
            )
        if _job_condition(job, "Complete") != "True":
            error = _api_probe(test_context)
            if error is not None:
                api_errors.append(error)
            overlapping = _migration_jobs_since(
                batch_api, state["exercise_started_at"]
            )
            if overlapping:
                state.setdefault("api_errors", {})[run_key] = api_errors
                pytest.fail(
                    "Operator migration Job(s) overlapped the prepopulation run: "
                    f"{sorted(overlapping)}"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        # Job completed: parse output, then take a final liveness sample.
        error = _api_probe(test_context)
        if error is not None:
            api_errors.append(error)
        state.setdefault("api_errors", {})[run_key] = api_errors
        state.setdefault("parsed_output", {})[run_key] = (
            _parse_prepopulation_output(_job_pod_logs(core_api, job_name))
        )
        state.setdefault("migration_overlap", {})[run_key] = _migration_jobs_since(
            batch_api, state["exercise_started_at"]
        )
        logger.info(
            "Prepopulation Job %s completed; parsed entity counters: %s",
            job_name,
            state["parsed_output"][run_key]["stats"],
        )
        return

    state.setdefault("api_errors", {})[run_key] = api_errors
    pytest.fail(
        f"Prepopulation Job {job_name} did not Complete within "
        f"{JOB_COMPLETE_TIMEOUT_SECONDS}s.\n"
        f"{_job_pod_status_diagnostics(core_api, job_name)}"
    )


def _job_pod_logs(core_api: client.CoreV1Api, job_name: str) -> str:
    """Fetch Job pod logs for parsing only; the text is never logged."""
    pods = core_api.list_namespaced_pod(
        _operator_namespace(), label_selector=f"job-name={job_name}"
    )
    for pod in pods.items:
        try:
            response = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=_operator_namespace(),
                container=PREPOPULATION_CONTAINER_NAME,
                _preload_content=False,
            )
            try:
                return response.data.decode("utf-8")
            finally:
                response.close()
                response.release_conn()
        except ApiException:
            continue
        except UnicodeError:
            pytest.fail("Could not decode prepopulation Job logs", pytrace=False)
    pytest.fail(
        f"Prepopulation Job {job_name} completed but its pod logs are unavailable"
    )


def action_prepare_trace_prepopulation(test_context: TestContext) -> None:
    """Resolve primary backend wiring and capture the pre-run state."""
    ClientManager.load_k8s_config()
    core_api = client.CoreV1Api()
    apps_api = client.AppsV1Api()

    state: dict[str, Any] = {
        "namespace": _operator_namespace(),
        "batch_size": PREPOPULATION_BATCH_SIZE,
        "exercise_started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "cr_before": _cr_snapshot(),
    }
    state.update(_effective_backend_secret(core_api, apps_api))
    state["database_pod"] = _wait_for_database_pod(core_api)

    revision = _query_revision(core_api, state)
    if revision != PREDECESSOR_SCHEMA_REVISION:
        pytest.fail(
            f"Deployment is not at the predecessor revision {PREDECESSOR_SCHEMA_REVISION!r} "
            f"required by the prepopulation command (found {revision!r})"
        )
    state["revision_before"] = revision
    state["trace_info_columns_before"] = {
        column: _analytics_column_exists(core_api, state, "trace_info", column)
        for column in ("trace_name", "session_id")
    }
    state["spans_columns_before"] = {
        column: _analytics_column_exists(core_api, state, "spans", column)
        for column in ("model_name", "model_provider")
    }
    state["payloads"] = {}
    state["parsed_output"] = {}
    state["api_errors"] = {}
    state["migration_overlap"] = {}
    test_context.prepopulation_state = state
    logger.info(
        "Prepared prepopulation exercise in namespace %s against database %s "
        "(revision=%s)",
        state["namespace"],
        state["database"],
        revision,
    )


def action_seed_initial_prepopulation_traces(test_context: TestContext) -> None:
    """Seed the first batch of traces with observable analytics values."""
    state = test_context.prepopulation_state
    state["payloads"]["initial"] = _seed_traces(
        test_context, state, TRACE_COUNT_INITIAL, "initial"
    )
    feedback = mlflow.log_feedback(
        trace_id=state["payloads"]["initial"][0]["trace_id"],
        name="prepopulation-score",
        value=0.75,
    )
    state["assessment_id"] = feedback.assessment_id


def action_run_first_prepopulation_job(test_context: TestContext) -> None:
    """Run the prepopulation Job for the first time against the live deployment."""
    _run_prepopulate_job(test_context, test_context.prepopulation_state, "first")


def action_seed_additional_prepopulation_traces(test_context: TestContext) -> None:
    """Seed a second batch, as a concurrent writer between prepopulation runs."""
    state = test_context.prepopulation_state
    state["payloads"]["additional"] = _seed_traces(
        test_context, state, TRACE_COUNT_ADDITIONAL, "additional"
    )


def action_run_second_prepopulation_job(test_context: TestContext) -> None:
    """Rerun the prepopulation Job to prove rerun idempotency at row level."""
    _run_prepopulate_job(test_context, test_context.prepopulation_state, "second")
