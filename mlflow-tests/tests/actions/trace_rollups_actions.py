"""Actions for the live SQL trace-rollup smoke flow."""

from __future__ import annotations

import base64
import logging
import re
import select
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import mlflow
import pytest
import psycopg2
import requests
from kubernetes import client
from kubernetes.client.rest import ApiException
from mlflow.entities import TraceState
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME
from psycopg2 import sql as pg_sql
from urllib3.exceptions import HTTPError

from mlflow_tests.utils.client import ClientManager, read_kubernetes_response
from mlflow_tests.utils.wait import wait_until

from ..constants.config import Config
from ..http_utils import get_mlflow_base_uri, get_requests_verify_value
from ..shared import TestContext
from .job_utils import (
    JOB_API_REQUEST_TIMEOUT_SECONDS,
    job_from_cronjob,
    wait_for_job_complete,
)

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
JOB_DELETE_TIMEOUT_SECONDS = 180
SQL_EXEC_TIMEOUT_SECONDS = 60
POSTGRES_PORT = 5432
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
ROLLUP_REBUILD_QUEUE_TABLE = "sql_trace_rollup_rebuild_queue"
ROLLUP_PROBE_COLUMNS = ("sample_count", "sum_value", "max_value", "min_value")

RollupProbeValues = dict[str, dict[str, int | float | None]]


def _seed_trace(
    admin_client: Any,
    experiment_id: str,
    index: int,
    start_time_ns: int,
    latency_ms: int,
    seeded_trace_ids: list[str],
) -> tuple[str, tuple[str, str]]:
    """Create a backdated two-span trace with a known latency."""
    trace_name = f"rollups-{index}"
    end_time_ns = start_time_ns + latency_ms * NS_PER_MS
    root_span = admin_client.start_trace(
        name=trace_name, experiment_id=experiment_id, start_time_ns=start_time_ns
    )
    trace_id = getattr(root_span, "request_id", None) or getattr(
        root_span, "trace_id", None
    )
    if not trace_id or trace_id == NO_OP_SPAN_TRACE_ID:
        raise RuntimeError(
            f"start_trace returned an unusable trace ID {trace_id!r} for "
            f"trace {trace_name!r} in experiment {experiment_id!r}"
        )
    # Register the root before creating the child so partial traces are cleaned up.
    seeded_trace_ids.append(trace_id)
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
    return trace_id, (root_span.span_id, child_span.span_id)


def _seed_trace_batch(
    test_context: TestContext,
    *,
    start_times_ms: list[int],
    latencies_ms: list[int],
    index_offset: int,
    batch_description: str,
) -> tuple[list[str], list[int], dict[str, tuple[str, str]]]:
    """Seed traces and return IDs, verified durations, and expected span IDs."""
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before seeding traces")
    experiment_id = test_context.active_experiment_id
    if not experiment_id:
        raise ValueError(
            "test_context.active_experiment_id must be set before seeding traces"
        )

    seeded_trace_ids = test_context.trace_rollups_state.setdefault("trace_ids", [])
    seeded_traces = [
        _seed_trace(
            admin_client=test_context.user_client,
            experiment_id=experiment_id,
            index=index_offset + index,
            start_time_ns=start_time_ms * NS_PER_MS,
            latency_ms=latency_ms,
            seeded_trace_ids=seeded_trace_ids,
        )
        for index, (start_time_ms, latency_ms) in enumerate(
            zip(start_times_ms, latencies_ms, strict=True)
        )
    ]
    trace_ids = [trace_id for trace_id, _ in seeded_traces]
    span_ids_by_trace_id = {
        trace_id: span_ids for trace_id, span_ids in seeded_traces
    }
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
    return trace_ids, observed_latencies_ms, span_ids_by_trace_id


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


def _start_database_port_forward(
    database_pod: str,
) -> tuple[subprocess.Popen[str], int]:
    process = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "--address",
            "127.0.0.1",
            "-n",
            Config.MLFLOW_NAMESPACE,
            f"pod/{database_pod}",
            f":{POSTGRES_PORT}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + SQL_EXEC_TIMEOUT_SECONDS
    forwarding_re = re.compile(r"Forwarding from 127\.0\.0\.1:(\d+) -> 5432")
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None or process.stdout is None:
                raise RuntimeError("Could not start the PostgreSQL port-forward")
            readable, _, _ = select.select([process.stdout], [], [], 0.2)
            if not readable:
                continue
            line = process.stdout.readline()
            match = forwarding_re.search(line)
            if match:
                return process, int(match.group(1))
        raise TimeoutError("Timed out starting the PostgreSQL port-forward")
    except Exception:
        _stop_database_port_forward(process)
        raise


def _stop_database_port_forward(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def _postgres_ca_file(core_api: client.CoreV1Api, state: dict[str, Any]) -> str:
    configured_ca = Config.CA_BUNDLE
    if configured_ca and Path(configured_ca).is_file():
        return configured_ca

    config_map = core_api.read_namespaced_config_map(
        name="mlflow-ca-bundle", namespace=Config.MLFLOW_NAMESPACE
    )
    certificates = [
        value
        for key, value in sorted((config_map.data or {}).items())
        if key.endswith((".crt", ".pem"))
    ]
    if not certificates:
        raise RuntimeError(
            "The PostgreSQL CA bundle ConfigMap contains no certificates"
        )

    ca_directory = tempfile.TemporaryDirectory(prefix="mlflow-trace-rollups-ca-")
    ca_path = Path(ca_directory.name) / "postgres-ca-bundle.pem"
    ca_path.write_text("\n".join(certificates), encoding="utf-8")
    state["database_ca_directory"] = ca_directory
    return str(ca_path)


def _database_connection(
    *,
    backend_uri: str,
    local_port: int,
    core_api: client.CoreV1Api,
    state: dict[str, Any],
) -> psycopg2.extensions.connection:
    try:
        connection_parameters = psycopg2.extensions.parse_dsn(backend_uri)
    except (psycopg2.Error, ValueError):
        raise RuntimeError("Could not parse the harness PostgreSQL URI") from None
    connection_parameters.update(
        {
            # Keep the URI hostname for TLS name verification while routing the
            # socket through kubectl's loopback-only port-forward.
            "hostaddr": "127.0.0.1",
            "port": str(local_port),
            "connect_timeout": "10",
        }
    )
    if connection_parameters.get("sslmode") in {"verify-ca", "verify-full"} and not (
        connection_parameters.get("sslrootcert")
    ):
        connection_parameters["sslrootcert"] = _postgres_ca_file(core_api, state)
    try:
        connection = psycopg2.connect(**connection_parameters)
    except (psycopg2.Error, ValueError):
        raise RuntimeError(
            "Could not connect to the harness PostgreSQL database"
        ) from None
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (f"{SQL_EXEC_TIMEOUT_SECONDS}s",),
            )
    except psycopg2.Error:
        connection.close()
        raise RuntimeError(
            "Could not configure the harness PostgreSQL query timeout"
        ) from None
    return connection


def _database_query(
    *,
    state: dict[str, Any],
    sql: str,
    params: tuple[Any, ...] = (),
) -> str:
    """Execute a SQL statement with psycopg2's server-side parameter binding."""
    try:
        with state["database_connection"].cursor() as cursor:
            cursor.execute(sql, params)
            if cursor.description is None:
                return ""
            rows = cursor.fetchall()
    except psycopg2.Error:
        raise RuntimeError("Trace-rollup database query failed") from None

    def format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "t" if value else "f"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    return "\n".join("|".join(format_value(value) for value in row) for row in rows)


def _mlflow_cr(custom_api: client.CustomObjectsApi) -> dict[str, Any]:
    return custom_api.get_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name="mlflow",
    )


def _rollup_job_is_deleted(batch_api: client.BatchV1Api, name: str) -> bool:
    try:
        read_kubernetes_response(
            batch_api.api_client,
            batch_api.read_namespaced_job,
            "V1Job",
            name=name,
            namespace=Config.MLFLOW_NAMESPACE,
            _request_timeout=JOB_API_REQUEST_TIMEOUT_SECONDS,
        )
    except ApiException as exc:
        if exc.status == 404:
            return True
        raise ApiException(status=exc.status) from None
    return False


def _delete_rollup_job(batch_api: client.BatchV1Api, name: str) -> None:
    delete_requested = False

    @wait_until(
        description=f"trace-rollup Job {name} to be deleted",
        timeout=JOB_DELETE_TIMEOUT_SECONDS,
        interval=POLL_INTERVAL_SECONDS,
        until=lambda deleted: deleted,
        retry_rules={
            ApiException: lambda exc: (exc.status or 0) in {0, 409, 429}
            or (exc.status or 0) >= 500,
            HTTPError: None,
        },
    )
    def delete_and_wait() -> bool:
        nonlocal delete_requested
        if not delete_requested:
            try:
                batch_api.delete_namespaced_job(
                    name=name,
                    namespace=Config.MLFLOW_NAMESPACE,
                    propagation_policy="Foreground",
                    _request_timeout=JOB_API_REQUEST_TIMEOUT_SECONDS,
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise
            # Only start checking absence after the DELETE succeeded or returned 404.
            delete_requested = True

        return _rollup_job_is_deleted(batch_api=batch_api, name=name)

    delete_and_wait()


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


def _read_backend_uri(core_api: client.CoreV1Api, namespace: str) -> str:
    try:
        secret = read_kubernetes_response(
            core_api.api_client,
            core_api.read_namespaced_secret,
            "V1Secret",
            name=DB_CREDENTIALS_SECRET_NAME,
            namespace=namespace,
            _request_timeout=JOB_API_REQUEST_TIMEOUT_SECONDS,
        )
    except (ApiException, HTTPError, ValueError):
        raise RuntimeError("Could not read the harness PostgreSQL Secret") from None

    encoded_uri = (secret.data or {}).get(BACKEND_STORE_URI_SECRET_KEY)
    if not encoded_uri:
        pytest.fail(
            f"Harness Secret {DB_CREDENTIALS_SECRET_NAME!r} has no "
            f"{BACKEND_STORE_URI_SECRET_KEY!r} key"
        )
    try:
        return base64.b64decode(encoded_uri, validate=True).decode("utf-8")
    except ValueError:
        raise RuntimeError("Harness PostgreSQL Secret has an invalid encoded URI") from None


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

    backend_uri = _read_backend_uri(core_api, namespace)
    try:
        parsed_uri = urlparse(backend_uri)
    except ValueError:
        raise RuntimeError("Could not parse the harness PostgreSQL URI") from None
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

    try:
        cronjob = batch_api.read_namespaced_cron_job(
            name=ROLLUPS_CRONJOB_NAME, namespace=namespace
        )
    except ApiException as exc:
        if exc.status == 404:
            exc.reason = (
                f"{exc.reason or 'Not Found'}\n"
                "The eligible trace-rollup smoke test expected the MLflow operator to "
                f"create CronJob {ROLLUPS_CRONJOB_NAME!r} in namespace {namespace!r}."
            )
        raise
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
    port_forward, local_port = _start_database_port_forward(database_pod)
    state.update(
        {
            "database": database,
            "database_user": database_user,
            "database_pod": database_pod,
            "database_port_forward": port_forward,
        }
    )
    state["database_connection"] = _database_connection(
        backend_uri=backend_uri,
        local_port=local_port,
        core_api=core_api,
        state=state,
    )
    current_database = _database_query(
        state=state,
        sql="SELECT current_database()",
    )
    if current_database != database:
        pytest.fail(
            f"PostgreSQL connected to {current_database!r}, expected harness database {database!r}"
        )

    state.update(
        {
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
    state["experiment_id"] = test_context.active_experiment_id
    workspace = test_context.active_workspace
    current_utc_day = time.time_ns() // NS_PER_MS // MS_PER_DAY
    start_time_ms = (current_utc_day - BACKDATE_DAYS) * MS_PER_DAY
    boundary_ms = start_time_ms + MS_PER_DAY
    start_times_ms = [
        boundary_ms - 1000 + index for index in range(TRACE_COUNT - 1)
    ] + [boundary_ms]
    trace_ids, observed_latencies_ms, span_ids_by_trace_id = _seed_trace_batch(
        test_context,
        start_times_ms=start_times_ms,
        latencies_ms=EXPECTED_TRACE_LATENCIES_MS,
        index_offset=0,
        batch_description="Seeded",
    )
    state.update(
        {
            "workspace": workspace,
            "expected_span_ids_by_trace_id": span_ids_by_trace_id,
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
    existing_trace_ids = list(state.get("trace_ids", []))
    if len(existing_trace_ids) != TRACE_COUNT:
        raise ValueError(
            f"Expected the initial {TRACE_COUNT} traces before adding more, "
            f"found {len(existing_trace_ids)}"
        )

    # Rebuild the first UTC day while leaving the second day's traces unchanged.
    additional_start_time_ms = state["start_time_ms"] + len(existing_trace_ids) + 1
    (
        additional_trace_ids,
        observed_latencies_ms,
        additional_span_ids_by_trace_id,
    ) = _seed_trace_batch(
        test_context,
        start_times_ms=[
            additional_start_time_ms + index for index in range(ADDITIONAL_TRACE_COUNT)
        ],
        latencies_ms=ADDITIONAL_TRACE_LATENCIES_MS,
        index_offset=len(existing_trace_ids),
        batch_description="Additional",
    )

    state["additional_trace_ids"] = additional_trace_ids
    state["trace_ids_before_additional_seed"] = existing_trace_ids
    state["additional_start_time_ms"] = additional_start_time_ms
    state["expected_span_ids_by_trace_id"].update(additional_span_ids_by_trace_id)
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
    # Register before submitting. The rollup finalizer owns both Job and SQL cleanup.
    state["test_created_job_names"].append(job_name)
    batch_api.create_namespaced_job(namespace=namespace, body=job)
    wait_for_job_complete(
        batch_api,
        state["core_api"],
        job_name,
        namespace,
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
        WHERE experiment_id = %s
          AND grouping_set = 'global'
          AND rollup_day BETWEEN %s AND %s
        GROUP BY rollup_day
    """
    output = _database_query(
        state=state,
        sql=sql,
        params=(int(experiment_id), first_day, last_day),
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


def _read_rollup_probe_rows(
    cursor: psycopg2.extensions.cursor,
    experiment_id: int,
    day: date,
) -> RollupProbeValues:
    columns = ", ".join(ROLLUP_PROBE_COLUMNS)
    cursor.execute(
        f"""
            SELECT metric_name, {columns}
            FROM {ROLLUP_TABLE}
            WHERE experiment_id = %s
              AND rollup_day = %s
              AND grouping_set = 'global'
              AND metric_name IN ('trace_count', 'latency')
            FOR UPDATE
        """,
        (experiment_id, day),
    )
    rows = cursor.fetchall()
    metric_names = [row[0] for row in rows]
    if sorted(metric_names) != ["latency", "trace_count"]:
        raise RuntimeError(
            f"Expected exactly one {ROLLUP_TABLE} row per metric for "
            f"experiment {experiment_id} on {day}; got {metric_names}"
        )
    return {
        metric: dict(zip(ROLLUP_PROBE_COLUMNS, values, strict=True))
        for metric, *values in rows
    }


def _update_rollup_probe_rows(
    cursor: psycopg2.extensions.cursor,
    experiment_id: int,
    day: date,
    changes: RollupProbeValues,
) -> None:
    for metric, values in changes.items():
        assignments = pg_sql.SQL(", ").join(
            pg_sql.SQL("{} = %s").format(pg_sql.Identifier(column)) for column in values
        )
        cursor.execute(
            pg_sql.SQL(
                """
                    UPDATE {table}
                    SET {assignments}
                    WHERE experiment_id = %s
                      AND rollup_day = %s
                      AND grouping_set = 'global'
                      AND metric_name = %s
                """
            ).format(table=pg_sql.Identifier(ROLLUP_TABLE), assignments=assignments),
            (*values.values(), experiment_id, day, metric),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Expected one updated {ROLLUP_TABLE} row for "
                f"{metric} in experiment {experiment_id} on {day}; got {cursor.rowcount}"
            )


@contextmanager
def _probe_rollup_values(
    state: dict[str, Any],
    experiment_id: int,
    day: date,
    changes: RollupProbeValues,
) -> Iterator[None]:
    """Commit the probe for API readers and restore the original values afterward."""
    connection = state["database_connection"]
    restore_values = None
    try:
        try:
            with connection, connection.cursor() as cursor:
                original_rows = _read_rollup_probe_rows(cursor, experiment_id, day)
                expected_rows = {
                    metric: values | changes.get(metric, {})
                    for metric, values in original_rows.items()
                }
                _update_rollup_probe_rows(cursor, experiment_id, day, changes)
                probed_rows = _read_rollup_probe_rows(cursor, experiment_id, day)
                assert probed_rows == expected_rows, (
                    f"Unexpected trace-rollup probe rows on {day}: "
                    f"{probed_rows} != {expected_rows}"
                )
                restore_values = {
                    metric: {column: original_rows[metric][column] for column in values}
                    for metric, values in changes.items()
                }
            yield
        finally:
            if restore_values is not None:
                with connection, connection.cursor() as cursor:
                    _update_rollup_probe_rows(
                        cursor, experiment_id, day, restore_values
                    )
                    restored_rows = _read_rollup_probe_rows(cursor, experiment_id, day)
                    assert restored_rows == original_rows, (
                        f"Trace-rollup probe rows were not restored on {day}: "
                        f"{restored_rows} != {original_rows}"
                    )
    except psycopg2.Error:
        raise RuntimeError("Trace-rollup probe query failed") from None


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
    for day, (trace_count, _, _, _) in state["observed_sql_rollups"].items():
        with _probe_rollup_values(
            state,
            int(experiment_id),
            day,
            {
                "trace_count": {"sample_count": trace_count + probe_count_delta},
                "latency": {"max_value": probe_latency_max},
            },
        ):
            probed_count, probed_latency = _read_trace_rollup_metrics(
                experiment_id, state
            )
            assert probed_count.get("COUNT") == expected_probe_count, (
                f"Trace-count API did not use the materialized value for {day}: {probed_count}"
            )
            assert probed_latency.get("MAX") == probe_latency_max, (
                f"Latency API did not use the materialized value for {day}: {probed_latency}"
            )

    probe_latency_sum_delta = 1000
    expected_probe_average = (
        state["expected"]["latency_sum"] + probe_latency_sum_delta
    ) / state["expected"]["trace_count"]
    probe_latency_min = state["expected"]["latency_min"] - 1
    # Change the latency row's own sum and minimum so raw-trace fallback cannot
    # satisfy these aggregations while the source traces remain unchanged.
    for day, (_, latency_sum, _, _) in state["observed_sql_rollups"].items():
        with _probe_rollup_values(
            state,
            int(experiment_id),
            day,
            {
                "latency": {
                    "sum_value": latency_sum + probe_latency_sum_delta,
                    "min_value": probe_latency_min,
                }
            },
        ):
            _, probed_latency = _read_trace_rollup_metrics(experiment_id, state)
            assert probed_latency.get("AVG") == pytest.approx(expected_probe_average), (
                f"Latency API did not use the materialized sum for {day}: {probed_latency}"
            )
            assert probed_latency.get("MIN") == probe_latency_min, (
                f"Latency API did not use the materialized minimum for {day}: "
                f"{probed_latency}"
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
    """Reload all seeded trace data and spans after rollup processing."""
    traces = test_context.user_client.search_traces(
        experiment_ids=[test_context.active_experiment_id],
        max_results=100,
        include_spans=True,
    )
    test_context.trace_rollups_state["observed_traces"] = traces
    test_context.trace_rollups_state["trace_status_passes"].append(
        {trace.info.trace_id: trace.info.status for trace in traces}
    )
    logger.info(
        "Read %s source traces after rollup processing",
        len(traces),
    )


def _delete_seeded_traces(
    test_context: TestContext, experiment_id: str, trace_ids: list[str]
) -> None:
    mlflow.flush_trace_async_logging()
    user_client = test_context.user_client
    deleted = user_client.delete_traces(
        experiment_id=experiment_id, trace_ids=trace_ids
    )
    if deleted != len(trace_ids):
        # Partial seeds may never reach the store, and retries may find IDs already gone.
        traces = user_client.search_traces(
            experiment_ids=[experiment_id], max_results=100, include_spans=False
        )
        remaining_ids = set(trace_ids).intersection(
            trace.info.trace_id for trace in traces
        )
        if remaining_ids:
            raise RuntimeError(f"Seeded traces still present: {sorted(remaining_ids)}")
    logger.info("Cleaned up seeded traces in smoke experiment %s", experiment_id)


def action_cleanup_trace_rollups(test_context: TestContext) -> None:
    """Remove this smoke test's Jobs, source traces, and database rollup state."""
    state = test_context.trace_rollups_state
    if not state:
        return

    cleanup_errors: list[str] = []
    for job_name in reversed(state.get("test_created_job_names", [])):
        try:
            _delete_rollup_job(batch_api=state["batch_api"], name=job_name)
            logger.info("Deleted test-created rollup Job %s", job_name)
        except Exception as exc:
            cleanup_errors.append(
                f"Could not confirm deletion of rollup Job {job_name}: {exc}"
            )

    experiment_id = state.get("experiment_id")
    trace_ids = state.get("trace_ids")
    if experiment_id and cleanup_errors:
        logger.warning(
            "Skipped trace and SQL cleanup for experiment %s because rollup Job deletion "
            "could not be confirmed",
            experiment_id,
        )
    if experiment_id and trace_ids and not cleanup_errors:
        try:
            # Deleting traces can enqueue rebuilds. Clear the queue after the deletion.
            _delete_seeded_traces(test_context, experiment_id, trace_ids)
        except Exception as exc:
            cleanup_errors.append(
                f"Could not clean up seeded traces in experiment {experiment_id}: {exc}"
            )
    if (
        experiment_id
        and state.get("database_pod")
        and state.get("database")
        and state.get("database_connection")
        and not cleanup_errors
    ):
        try:
            # Keep rebuild entries if the summary deletion fails.
            with state["database_connection"]:
                for table in (ROLLUP_REBUILD_QUEUE_TABLE, ROLLUP_TABLE):
                    table_exists = _database_query(
                        state=state,
                        sql="SELECT to_regclass(%s) IS NOT NULL",
                        params=(table,),
                    )
                    if table_exists not in {"t", "f"}:
                        raise RuntimeError(
                            f"Unexpected existence result for {table}: {table_exists!r}"
                        )
                    if table_exists == "t":
                        _database_query(
                            state=state,
                            sql=f"DELETE FROM {table} WHERE experiment_id = %s",
                            params=(int(experiment_id),),
                        )
            logger.info(
                "Cleaned up trace-rollup rows and rebuild entries for smoke experiment %s",
                experiment_id,
            )
        except Exception as exc:
            cleanup_errors.append(str(exc))

    connection = state.pop("database_connection", None)
    if connection is not None:
        connection.close()
    _stop_database_port_forward(state.pop("database_port_forward", None))
    ca_directory = state.pop("database_ca_directory", None)
    if ca_directory is not None:
        ca_directory.cleanup()

    if cleanup_errors:
        raise RuntimeError(
            "Trace-rollup smoke cleanup failed: " + "; ".join(cleanup_errors)
        )
