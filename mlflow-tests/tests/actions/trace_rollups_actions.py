"""Actions for the live SQL trace-rollup smoke flow."""

from __future__ import annotations

import base64
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import mlflow
import pytest
import requests
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from mlflow.entities import TraceState
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME

from mlflow_tests.utils.client import ClientManager
from mlflow_tests.utils.wait import wait_until

from ..constants.config import Config
from ..http_utils import get_mlflow_base_uri, get_requests_verify_value
from ..shared import TestContext
from .job_utils import job_from_cronjob, wait_for_job_complete

logger = logging.getLogger(__name__)

ROLLUPS_CRONJOB_NAME = "mlflow-trace-rollups"
ROLLUPS_CONTAINER_NAME = "mlflow-trace-rollups"
DB_CREDENTIALS_SECRET_NAME = "mlflow-db-credentials"
BACKEND_STORE_URI_SECRET_KEY = "backend-store-uri"
BACKEND_STORE_ENV_NAME = "MLFLOW_BACKEND_STORE_URI"
MLFLOW_API_GROUP = "mlflow.opendatahub.io"
MLFLOW_API_VERSION = "v1"
MLFLOW_API_PLURAL = "mlflows"
POSTGRES_POD_LABEL = "app=mlflow-postgres"
DATABASE_POD_TIMEOUT_SECONDS = 120
JOB_TIMEOUT_SECONDS = 180
SQL_EXEC_TIMEOUT_SECONDS = 60
TRACE_VISIBILITY_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 2
EXPECTED_TRACE_LATENCIES_MS = [10, 20, 30, 40, 50]
TRACE_COUNT = len(EXPECTED_TRACE_LATENCIES_MS)
ADDITIONAL_TRACE_LATENCIES_MS = [60, 70]
ADDITIONAL_TRACE_COUNT = len(ADDITIONAL_TRACE_LATENCIES_MS)
# Rollups process only UTC days older than their 24-hour inactivity cutoff.
BACKDATE_DAYS = 3
MS_PER_DAY = 86_400_000
NS_PER_MS = 1_000_000
ROLLUP_TABLE = "sql_trace_metric_daily_rollups"


def _seed_trace(
    admin_client: Any,
    experiment_id: str,
    index: int,
    start_time_ns: int,
    latency_ms: int,
) -> str:
    """Create a backdated two-span trace with a known latency."""
    trace_name = f"rollups-{index}"
    end_time_ns = start_time_ns + latency_ms * NS_PER_MS
    root_span = admin_client.start_trace(
        name=trace_name, experiment_id=experiment_id, start_time_ns=start_time_ns
    )
    trace_id = getattr(root_span, "request_id", None) or getattr(
        root_span, "trace_id", None
    )
    child_span = admin_client.start_span(
        name=f"{trace_name}-child",
        trace_id=trace_id,
        parent_id=root_span.span_id,
        start_time_ns=start_time_ns + NS_PER_MS,
    )
    admin_client.end_span(
        trace_id=trace_id,
        span_id=child_span.span_id,
        status="OK",
        end_time_ns=end_time_ns - NS_PER_MS,
    )
    admin_client.end_trace(
        trace_id=trace_id,
        status="OK",
        end_time_ns=end_time_ns,
    )
    return trace_id


def _seed_trace_batch(
    test_context: TestContext,
    *,
    start_times_ms: list[int],
    latencies_ms: list[int],
    index_offset: int,
    batch_description: str,
) -> tuple[list[str], list[int]]:
    """Seed a batch and return its IDs and verified durations from the trace API."""
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before seeding traces")
    experiment_id = test_context.active_experiment_id
    if not experiment_id:
        raise ValueError(
            "test_context.active_experiment_id must be set before seeding traces"
        )

    trace_ids = [
        _seed_trace(
            admin_client=test_context.user_client,
            experiment_id=experiment_id,
            index=index_offset + index,
            start_time_ns=start_time_ms * NS_PER_MS,
            latency_ms=latency_ms,
        )
        for index, (start_time_ms, latency_ms) in enumerate(
            zip(start_times_ms, latencies_ms, strict=True)
        )
    ]
    # Finish exporting spans as well as trace metadata before the rollup Job starts.
    mlflow.flush_trace_async_logging()
    _, duration_by_id = _wait_for_seeded_trace_durations(
        user_client=test_context.user_client,
        experiment_id=experiment_id,
        trace_ids=trace_ids,
    )
    observed_latencies_ms: list[int] = []
    for trace_id, expected_duration in zip(trace_ids, latencies_ms, strict=True):
        observed_duration = duration_by_id[trace_id]
        if observed_duration is None or observed_duration != expected_duration:
            pytest.fail(
                f"{batch_description} trace {trace_id} has duration "
                f"{observed_duration!r} ms, expected {expected_duration} ms"
            )
        observed_latencies_ms.append(observed_duration)
    return trace_ids, observed_latencies_ms


def _expected_rollup_values(latencies_ms: list[int]) -> dict[str, int]:
    return {
        "trace_count": len(latencies_ms),
        "latency_sum": sum(latencies_ms),
        "latency_max": max(latencies_ms),
        "latency_min": min(latencies_ms),
    }


@wait_until(
    description="seeded traces to finish and become searchable",
    timeout=TRACE_VISIBILITY_TIMEOUT_SECONDS,
    interval=POLL_INTERVAL_SECONDS,
    until=lambda result: not result[0],
)
def _wait_for_seeded_trace_durations(
    user_client: Any, experiment_id: str, trace_ids: list[str]
) -> tuple[set[str], dict[str, int | None]]:
    """Return pending IDs and the durations of finished seeded traces."""
    expected = set(trace_ids)
    traces = user_client.search_traces(
        experiment_ids=[experiment_id], max_results=100, include_spans=False
    )
    finished_states = {TraceState.OK, TraceState.ERROR}
    # Incremental child-span export can make a trace searchable before the root span ends.
    duration_by_id = {
        trace.info.trace_id: trace.info.execution_duration
        for trace in traces
        if trace.info.trace_id in expected
        and trace.info.state in finished_states
    }
    return expected - duration_by_id.keys(), duration_by_id


@wait_until(
    description=f"a running PostgreSQL pod with label {POSTGRES_POD_LABEL!r}",
    timeout=DATABASE_POD_TIMEOUT_SECONDS,
    interval=POLL_INTERVAL_SECONDS,
    until=lambda pod_name: pod_name is not None,
)
def _database_pod(core_api: client.CoreV1Api) -> str | None:
    pods = core_api.list_namespaced_pod(
        namespace=Config.MLFLOW_NAMESPACE, label_selector=POSTGRES_POD_LABEL
    )
    return next(
        (
            pod.metadata.name
            for pod in pods.items
            if pod.status.phase == "Running" and pod.metadata.name
        ),
        None,
    )


def _backend_uses_harness_secret(env_vars: list[client.V1EnvVar] | None) -> bool:
    for env_var in reversed(env_vars or []):
        if env_var.name == BACKEND_STORE_ENV_NAME:
            secret_ref = env_var.value_from and env_var.value_from.secret_key_ref
            return (
                secret_ref is not None
                and secret_ref.name == DB_CREDENTIALS_SECRET_NAME
                and secret_ref.key == BACKEND_STORE_URI_SECRET_KEY
            )
    return False


def _database_query(
    *,
    core_api: client.CoreV1Api,
    database_pod: str,
    db_name: str,
    db_user: str,
    sql: str,
) -> str:
    command = [
        "psql",
        "-X",
        "--no-password",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        db_user,
        "-d",
        db_name,
        "-t",
        "-A",
        "-c",
        sql,
    ]
    response = stream(
        core_api.connect_get_namespaced_pod_exec,
        name=database_pod,
        namespace=Config.MLFLOW_NAMESPACE,
        command=command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
        _request_timeout=SQL_EXEC_TIMEOUT_SECONDS,
    )
    try:
        response.run_forever(timeout=SQL_EXEC_TIMEOUT_SECONDS)
        if response.is_open():
            raise TimeoutError(f"psql did not complete within {SQL_EXEC_TIMEOUT_SECONDS}s")
        try:
            exit_code = response.returncode
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("Kubernetes exec returned no usable psql exit status") from exc
        if exit_code != 0:
            stderr = response.read_stderr().strip()
            raise RuntimeError(f"psql exited with status {exit_code}: {stderr}")
        return response.read_stdout().strip()
    finally:
        response.close()


def _mlflow_cr(custom_api: client.CustomObjectsApi) -> dict[str, Any]:
    return custom_api.get_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name="mlflow",
    )


def _delete_rollup_job(batch_api: client.BatchV1Api, name: str) -> None:
    try:
        batch_api.delete_namespaced_job(
            name=name,
            namespace=Config.MLFLOW_NAMESPACE,
            propagation_policy="Foreground",
        )
    except ApiException as exc:
        if exc.status != 404:
            raise
    _wait_for_rollup_job_deletion(batch_api=batch_api, name=name)


@wait_until(
    description="test-created trace-rollup Job to be deleted",
    timeout=JOB_TIMEOUT_SECONDS,
    interval=POLL_INTERVAL_SECONDS,
    until=lambda deleted: deleted,
    retry_rules={
        ApiException: lambda exc: exc.status in {409, 429} or (exc.status or 0) >= 500
    },
)
def _wait_for_rollup_job_deletion(batch_api: client.BatchV1Api, name: str) -> bool:
    try:
        batch_api.read_namespaced_job(name=name, namespace=Config.MLFLOW_NAMESPACE)
    except ApiException as exc:
        if exc.status == 404:
            return True
        raise
    return False


def _trace_metric_values(
    *, experiment_id: str, start_time_ms: int, end_time_ms: int,
    metric_name: str, aggregations: list[str], workspace: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{get_mlflow_base_uri()}/api/3.0/mlflow/traces/metrics",
        json={
            "experiment_ids": [experiment_id],
            "view_type": "TRACES",
            "metric_name": metric_name,
            "aggregations": [
                {"aggregation_type": aggregation} for aggregation in aggregations
            ],
            "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
        },
        headers={
            "Authorization": f"Bearer {Config.K8_API_TOKEN}",
            WORKSPACE_HEADER_NAME: workspace,
        },
        verify=get_requests_verify_value(),
        timeout=Config.REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data_points = response.json().get("data_points", [])
    assert len(data_points) == 1, f"Unexpected {metric_name} data points: {data_points}"
    return data_points[0]["values"]


def action_prepare_trace_rollups(test_context: TestContext) -> None:
    """Capture the harness backend and verify tracking and CronJob wiring."""
    ClientManager.load_k8s_config()
    namespace = Config.MLFLOW_NAMESPACE
    core_api = client.CoreV1Api()
    apps_api = client.AppsV1Api()
    custom_api = client.CustomObjectsApi()
    batch_api = client.BatchV1Api()
    state: dict[str, Any] = {
        "namespace": namespace,
        "core_api": core_api,
        "batch_api": batch_api,
    }
    test_context.trace_rollups_state = state

    secret = core_api.read_namespaced_secret(
        name=DB_CREDENTIALS_SECRET_NAME, namespace=namespace
    )
    encoded_uri = (secret.data or {}).get(BACKEND_STORE_URI_SECRET_KEY)
    if not encoded_uri:
        pytest.fail(
            f"Harness Secret {DB_CREDENTIALS_SECRET_NAME!r} has no "
            f"{BACKEND_STORE_URI_SECRET_KEY!r} key"
        )
    backend_uri = base64.b64decode(encoded_uri).decode("utf-8")
    parsed_uri = urlparse(backend_uri)
    database = unquote(parsed_uri.path.lstrip("/"))
    if parsed_uri.scheme not in {"postgres", "postgresql"} or not database:
        pytest.fail("Harness backend Secret does not contain a PostgreSQL URI with a database")
    database_user = unquote(parsed_uri.username or "")
    if not database_user:
        pytest.fail("Harness backend Secret does not contain a PostgreSQL username")

    mlflow_cr = _mlflow_cr(custom_api=custom_api)
    spec = mlflow_cr.get("spec", {})
    backend_ref = spec.get("backendStoreUriFrom") or {}
    annotations = mlflow_cr.get("metadata", {}).get("annotations") or {}
    if (
        spec.get("backendStoreUri")
        or backend_ref.get("name") != DB_CREDENTIALS_SECRET_NAME
        or backend_ref.get("key") != BACKEND_STORE_URI_SECRET_KEY
        or any(env.get("name") == BACKEND_STORE_ENV_NAME for env in spec.get("env") or [])
        or "mlflow.opendatahub.io/force-migrate" in annotations
    ):
        pytest.fail("Expected MLflow to use the harness backend without a pending migration")

    deployment = apps_api.read_namespaced_deployment(
        name="mlflow", namespace=namespace
    )
    tracking_container = next(
        (
            container
            for container in deployment.spec.template.spec.containers
            if container.name == "mlflow"
        ),
        None,
    )
    replicas = deployment.spec.replicas or 0
    deployment_status = deployment.status
    if (
        tracking_container is None
        or not _backend_uses_harness_secret(tracking_container.env)
        or replicas < 1
        or deployment_status.observed_generation < deployment.metadata.generation
        or (deployment_status.updated_replicas or 0) != replicas
        or (deployment_status.ready_replicas or 0) != replicas
    ):
        pytest.fail("Tracking Deployment is not ready on the harness backend Secret")

    cronjob = batch_api.read_namespaced_cron_job(
        name=ROLLUPS_CRONJOB_NAME, namespace=namespace
    )
    rollup_container = next(
        (
            container
            for container in cronjob.spec.job_template.spec.template.spec.containers
            if container.name == ROLLUPS_CONTAINER_NAME
        ),
        None,
    )
    if (
        rollup_container is None
        or not _backend_uses_harness_secret(rollup_container.env)
    ):
        pytest.fail("Trace-rollup CronJob does not use the harness backend Secret")
    if not cronjob.metadata.uid:
        pytest.fail("Trace-rollup CronJob has no UID")

    expected_schedule = "0 0 29 2 *"
    expected_time_zone = "Etc/UTC"
    rollups_spec = spec.get("traceRollups") or {}
    if (
        rollups_spec.get("schedule") != expected_schedule
        or rollups_spec.get("timeZone") != expected_time_zone
    ):
        pytest.fail(
            f"Expected the harness to set traceRollups.schedule={expected_schedule!r} "
            f"and traceRollups.timeZone={expected_time_zone!r}; got {rollups_spec!r}"
        )
    if (
        cronjob.spec.schedule != expected_schedule
        or cronjob.spec.time_zone != expected_time_zone
    ):
        pytest.fail(
            "Trace-rollup CronJob timing does not match the MLflow CR: "
            f"schedule={cronjob.spec.schedule!r} (expected {expected_schedule!r}), "
            f"time_zone={cronjob.spec.time_zone!r} (expected {expected_time_zone!r})"
        )

    database_pod = _database_pod(core_api=core_api)
    if not database_pod:
        pytest.fail("Harness PostgreSQL pod was not found")
    current_database = _database_query(
        core_api=core_api,
        database_pod=database_pod,
        db_name=database,
        db_user=database_user,
        sql="SELECT current_database()",
    )
    if current_database != database:
        pytest.fail(
            f"PostgreSQL connected to {current_database!r}, expected harness database {database!r}"
        )

    state.update(
        {
            "database": database,
            "database_user": database_user,
            "database_pod": database_pod,
            "backend_secret_name": DB_CREDENTIALS_SECRET_NAME,
            "cronjob": cronjob,
            "expected_schedule": expected_schedule,
            "expected_time_zone": expected_time_zone,
            "cronjob_schedule": cronjob.spec.schedule,
            "cronjob_time_zone": cronjob.spec.time_zone,
            "rollup_passes": 0,
            "rollup_job_names": [],
            "test_created_job_names": [],
            "expected_rollup_passes": [],
            "api_metric_passes": [],
            "trace_status_passes": [],
        }
    )
    logger.info(
        "Using harness PostgreSQL database %s; tracking and %s both reference Secret %s",
        database,
        ROLLUPS_CRONJOB_NAME,
        DB_CREDENTIALS_SECRET_NAME,
    )


def action_seed_trace_rollup_traces(test_context: TestContext) -> None:
    """Seed five traces across two complete historical UTC days."""
    state = test_context.trace_rollups_state
    workspace = test_context.active_workspace
    current_utc_day = time.time_ns() // NS_PER_MS // MS_PER_DAY
    start_time_ms = (current_utc_day - BACKDATE_DAYS) * MS_PER_DAY
    boundary_ms = start_time_ms + MS_PER_DAY
    start_times_ms = [
        boundary_ms - 1000 + index for index in range(TRACE_COUNT - 1)
    ] + [boundary_ms]
    trace_ids, observed_latencies_ms = _seed_trace_batch(
        test_context,
        start_times_ms=start_times_ms,
        latencies_ms=EXPECTED_TRACE_LATENCIES_MS,
        index_offset=0,
        batch_description="Seeded",
    )
    state.update(
        {
            "workspace": workspace,
            "experiment_id": test_context.active_experiment_id,
            "trace_ids": trace_ids,
            "trace_latencies_by_day": {
                _utc_day(start_time_ms): observed_latencies_ms[:-1],
                _utc_day(boundary_ms): observed_latencies_ms[-1:],
            },
            "start_time_ms": start_time_ms,
            "end_time_ms": boundary_ms + MS_PER_DAY - 1,
        }
    )
    state["expected"] = _expected_rollup_values(observed_latencies_ms)
    state["expected_rollup_passes"].append(dict(state["expected"]))
    logger.info(
        "Seeded %s backdated traces in experiment %s; trace IDs: %s",
        len(trace_ids),
        test_context.active_experiment_id,
        trace_ids,
    )


def action_seed_additional_trace_rollup_traces(test_context: TestContext) -> None:
    """Add two eligible traces on the already rolled-up UTC day."""
    state = test_context.trace_rollups_state
    existing_trace_ids = state.get("trace_ids") or []
    if len(existing_trace_ids) != TRACE_COUNT:
        raise ValueError(
            f"Expected the initial {TRACE_COUNT} traces before adding more, "
            f"found {len(existing_trace_ids)}"
        )

    # Rebuild the first UTC day while leaving the second day's traces unchanged.
    additional_start_time_ms = state["start_time_ms"] + len(existing_trace_ids) + 1
    additional_trace_ids, observed_latencies_ms = _seed_trace_batch(
        test_context,
        start_times_ms=[
            additional_start_time_ms + index for index in range(ADDITIONAL_TRACE_COUNT)
        ],
        latencies_ms=ADDITIONAL_TRACE_LATENCIES_MS,
        index_offset=len(existing_trace_ids),
        batch_description="Additional",
    )

    state["additional_trace_ids"] = additional_trace_ids
    state["trace_ids_before_additional_seed"] = list(existing_trace_ids)
    state["additional_start_time_ms"] = additional_start_time_ms
    state["trace_ids"].extend(additional_trace_ids)
    latencies_by_day = state["trace_latencies_by_day"]
    latencies_by_day[_utc_day(additional_start_time_ms)].extend(observed_latencies_ms)
    state["expected"] = _expected_rollup_values(
        [latency for latencies in latencies_by_day.values() for latency in latencies]
    )
    state["expected_rollup_passes"].append(dict(state["expected"]))
    logger.info(
        "Seeded %s additional traces in the existing UTC rollup day; "
        "combined trace count is %s; trace IDs: %s",
        len(additional_trace_ids),
        len(state["trace_ids"]),
        additional_trace_ids,
    )


def action_run_trace_rollup_job(test_context: TestContext) -> None:
    """Run a distinct one-off Job from the operator-managed CronJob template."""
    state = test_context.trace_rollups_state
    batch_api = state["batch_api"]
    namespace = state["namespace"]
    job_name = f"{ROLLUPS_CRONJOB_NAME}-smoke-{uuid.uuid4().hex[:12]}"
    cronjob = state["cronjob"]
    job = job_from_cronjob(cronjob, job_name, namespace)
    # Tie the Job to the CronJob so harness teardown also removes it if pytest exits early.
    job.metadata.owner_references = [
        client.V1OwnerReference(
            api_version="batch/v1",
            kind="CronJob",
            name=cronjob.metadata.name,
            uid=cronjob.metadata.uid,
        )
    ]
    # Register cleanup before submitting, including when creation succeeds but the response is lost.
    state["test_created_job_names"].append(job_name)
    test_context.add_job_for_cleanup(job_name, namespace)
    batch_api.create_namespaced_job(namespace=namespace, body=job)
    wait_for_job_complete(
        batch_api,
        state["core_api"],
        job_name,
        namespace,
        container_name=ROLLUPS_CONTAINER_NAME,
        job_description="SQL trace rollup",
    )
    state["rollup_passes"] += 1
    state["rollup_job_names"].append(job_name)


def _utc_day(time_ms: int) -> date:
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).date()


def _read_trace_rollup_rows(
    experiment_id: str, state: dict[str, Any]
) -> dict[date, tuple[int, ...]]:
    first_day = _utc_day(state["start_time_ms"])
    last_day = _utc_day(state["end_time_ms"])
    sql = f"""
        SELECT to_char(rollup_day, 'YYYY-MM-DD'),
               COALESCE(SUM(sample_count) FILTER (WHERE metric_name = 'trace_count'), 0),
               COALESCE(SUM(sum_value) FILTER (WHERE metric_name = 'latency'), 0),
               COALESCE(MAX(max_value) FILTER (WHERE metric_name = 'latency'), 0),
               COALESCE(MIN(min_value) FILTER (WHERE metric_name = 'latency'), 0)
        FROM {ROLLUP_TABLE}
        WHERE experiment_id = {int(experiment_id)}
          AND grouping_set = 'global'
          AND rollup_day BETWEEN DATE '{first_day}' AND DATE '{last_day}'
        GROUP BY rollup_day
    """
    output = _database_query(
        core_api=state["core_api"],
        database_pod=state["database_pod"],
        db_name=state["database"],
        db_user=state["database_user"],
        sql=sql,
    )
    observed: dict[date, tuple[int, ...]] = {}
    for line in output.splitlines():
        day, *metrics = line.split("|")
        observed[date.fromisoformat(day)] = tuple(map(int, metrics))
    return observed


def action_read_trace_rollup_rows(test_context: TestContext) -> None:
    """Read materialized trace metrics for each seeded UTC day."""
    state = test_context.trace_rollups_state
    experiment_id = test_context.active_experiment_id
    observed = _read_trace_rollup_rows(experiment_id, state)
    state["observed_sql_rollups"] = observed
    logger.info(
        "Observed SQL rollup values for experiment %s: %s",
        experiment_id,
        observed,
    )


def _read_trace_rollup_metrics(
    experiment_id: str, state: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    count_metrics = _trace_metric_values(
        experiment_id=experiment_id,
        start_time_ms=state["start_time_ms"],
        end_time_ms=state["end_time_ms"],
        metric_name="trace_count",
        aggregations=["COUNT"],
        workspace=state["workspace"],
    )
    latency_metrics = _trace_metric_values(
        experiment_id=experiment_id,
        start_time_ms=state["start_time_ms"],
        end_time_ms=state["end_time_ms"],
        metric_name="latency",
        aggregations=["AVG", "MAX", "MIN"],
        workspace=state["workspace"],
    )
    return count_metrics, latency_metrics


def _set_rollup_probe_values(
    state: dict[str, Any],
    experiment_id: str,
    day: date,
    *,
    trace_count: int | None = None,
    latency_sum: int | None = None,
    latency_max: int | None = None,
    latency_min: int | None = None,
) -> None:
    assignments: list[str] = []
    metric_names: set[str] = set()
    if trace_count is not None:
        assignments.append(
            "sample_count = CASE WHEN metric_name = 'trace_count' "
            f"THEN {trace_count} ELSE sample_count END"
        )
        metric_names.add("trace_count")
    if latency_sum is not None:
        assignments.append(
            "sum_value = CASE WHEN metric_name = 'latency' "
            f"THEN {latency_sum} ELSE sum_value END"
        )
        metric_names.add("latency")
    if latency_max is not None:
        assignments.append(
            "max_value = CASE WHEN metric_name = 'latency' "
            f"THEN {latency_max} ELSE max_value END"
        )
        metric_names.add("latency")
    if latency_min is not None:
        assignments.append(
            "min_value = CASE WHEN metric_name = 'latency' "
            f"THEN {latency_min} ELSE min_value END"
        )
        metric_names.add("latency")
    if not assignments:
        raise ValueError("At least one rollup probe value must be provided")

    metric_filter = ", ".join(f"'{name}'" for name in sorted(metric_names))
    expected_rows = "\n".join(f"{name}|1" for name in sorted(metric_names))
    set_clause = ", ".join(assignments)
    metric_rows = _database_query(
        core_api=state["core_api"],
        database_pod=state["database_pod"],
        db_name=state["database"],
        db_user=state["database_user"],
        sql=f"""
            WITH updated AS (
                UPDATE {ROLLUP_TABLE}
                SET {set_clause}
                WHERE experiment_id = {int(experiment_id)}
                  AND rollup_day = DATE '{day}' AND grouping_set = 'global'
                  AND metric_name IN ({metric_filter})
                RETURNING metric_name
            )
            SELECT metric_name, COUNT(*) FROM updated GROUP BY metric_name ORDER BY metric_name
        """,
    )
    assert metric_rows == expected_rows, (
        f"Expected one materialized row for each probe metric, got {metric_rows!r}; "
        f"expected {expected_rows!r}"
    )


def action_query_trace_rollup_metrics(test_context: TestContext) -> None:
    """Verify that the public metrics API reads the materialized summaries."""
    state = test_context.trace_rollups_state
    experiment_id = test_context.active_experiment_id
    count_metrics, latency_metrics = _read_trace_rollup_metrics(experiment_id, state)
    probe_count_delta = 100
    expected_probe_count = state["expected"]["trace_count"] + probe_count_delta
    probe_latency_max = state["expected"]["latency_max"] + 1000
    # Probe each day independently while querying the full window. Raw fallback for
    # either partition would omit that day's change.
    for day, (trace_count, _, latency_max, _) in state["observed_sql_rollups"].items():
        try:
            _set_rollup_probe_values(
                state,
                experiment_id,
                day,
                trace_count=trace_count + probe_count_delta,
                latency_max=probe_latency_max,
            )
            probed_count, probed_latency = _read_trace_rollup_metrics(experiment_id, state)
            assert probed_count.get("COUNT") == expected_probe_count, (
                f"Trace-count API did not use the materialized value for {day}: {probed_count}"
            )
            assert probed_latency.get("MAX") == probe_latency_max, (
                f"Latency API did not use the materialized value for {day}: {probed_latency}"
            )
        finally:
            _set_rollup_probe_values(
                state, experiment_id, day, trace_count=trace_count, latency_max=latency_max
            )

    probe_latency_sum_delta = 1000
    expected_probe_average = (
        state["expected"]["latency_sum"] + probe_latency_sum_delta
    ) / state["expected"]["trace_count"]
    probe_latency_min = state["expected"]["latency_min"] - 1
    # Change the latency row's own sum and minimum so raw-trace fallback cannot
    # satisfy these aggregations while the source traces remain unchanged.
    for day, (_, latency_sum, _, latency_min) in state["observed_sql_rollups"].items():
        try:
            _set_rollup_probe_values(
                state,
                experiment_id,
                day,
                latency_sum=latency_sum + probe_latency_sum_delta,
                latency_min=probe_latency_min,
            )
            _, probed_latency = _read_trace_rollup_metrics(experiment_id, state)
            assert probed_latency.get("AVG") == pytest.approx(expected_probe_average), (
                f"Latency API did not use the materialized sum for {day}: {probed_latency}"
            )
            assert probed_latency.get("MIN") == probe_latency_min, (
                f"Latency API did not use the materialized minimum for {day}: "
                f"{probed_latency}"
            )
        finally:
            _set_rollup_probe_values(
                state,
                experiment_id,
                day,
                latency_sum=latency_sum,
                latency_min=latency_min,
            )

    # Verify that cleanup restored both the stored summaries and their public API view.
    state["restored_sql_rollups"] = _read_trace_rollup_rows(experiment_id, state)
    (
        state["restored_count_metrics"],
        state["restored_latency_metrics"],
    ) = _read_trace_rollup_metrics(experiment_id, state)

    state["observed_count_metrics"] = count_metrics
    state["observed_latency_metrics"] = latency_metrics
    state["api_metric_passes"].append((count_metrics, latency_metrics))
    logger.info(
        "Observed public trace metrics: count=%s latency=%s",
        state["observed_count_metrics"],
        state["observed_latency_metrics"],
    )


def action_read_trace_rollup_sources(test_context: TestContext) -> None:
    """Reload all seeded trace metadata after rollup processing."""
    traces = test_context.user_client.search_traces(
        experiment_ids=[test_context.active_experiment_id],
        max_results=100,
        include_spans=False,
    )
    test_context.trace_rollups_state["observed_traces"] = traces
    test_context.trace_rollups_state["trace_status_passes"].append(
        {trace.info.trace_id: trace.info.status for trace in traces}
    )
    logger.info(
        "Read %s source traces after rollup processing",
        len(traces),
    )


def action_cleanup_trace_rollups(test_context: TestContext) -> None:
    """Remove this smoke test's Jobs before deleting its rollup rows."""
    state = test_context.trace_rollups_state
    if not state:
        return

    cleanup_errors: list[Exception] = []
    job_cleanup_failed = False
    for job_name in reversed(state.get("test_created_job_names", [])):
        try:
            _delete_rollup_job(batch_api=state["batch_api"], name=job_name)
            test_context.jobs_to_delete.pop(job_name, None)
            logger.info("Deleted test-created rollup Job %s", job_name)
        except Exception as exc:
            cleanup_errors.append(exc)
            job_cleanup_failed = True

    experiment_id = test_context.active_experiment_id
    if (
        experiment_id
        and state.get("database_pod")
        and state.get("database")
        and not job_cleanup_failed
    ):
        try:
            table_exists = _database_query(
                core_api=state["core_api"],
                database_pod=state["database_pod"],
                db_name=state["database"],
                db_user=state["database_user"],
                sql=f"SELECT to_regclass('{ROLLUP_TABLE}') IS NOT NULL",
            )
            if table_exists not in {"t", "f"}:
                raise RuntimeError(f"Unexpected rollup table existence result: {table_exists!r}")
            if table_exists == "t":
                _database_query(
                    core_api=state["core_api"],
                    database_pod=state["database_pod"],
                    db_name=state["database"],
                    db_user=state["database_user"],
                    sql=f"DELETE FROM {ROLLUP_TABLE} WHERE experiment_id = {int(experiment_id)}",
                )
                logger.info(
                    "Removed trace-rollup rows for smoke experiment %s",
                    experiment_id,
                )
        except Exception as exc:
            cleanup_errors.append(exc)

    if cleanup_errors:
        raise RuntimeError(
            "Trace-rollup smoke cleanup failed: "
            + "; ".join(str(error) for error in cleanup_errors)
        )
