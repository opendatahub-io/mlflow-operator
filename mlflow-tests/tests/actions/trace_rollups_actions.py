"""Actions for the live SQL trace-rollup smoke flow."""

from __future__ import annotations

import base64
import logging
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
import requests
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME

from mlflow_tests.utils.client import ClientManager
from mlflow_tests.utils.wait import WaitTimeoutError, wait_until

from ..constants.config import Config
from ..http_utils import get_mlflow_base_uri, get_requests_verify_value
from ..shared import TestContext
from .job_utils import wait_for_job_complete

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
CRONJOB_START_TIMEOUT_SECONDS = 180
CRONJOB_RECONCILE_TIMEOUT_SECONDS = 120
TRACE_VISIBILITY_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 2
EXPECTED_TRACE_LATENCIES_MS = [10, 20, 30, 40, 50]
TRACE_COUNT = len(EXPECTED_TRACE_LATENCIES_MS)
ADDITIONAL_TRACE_LATENCIES_MS = [60, 70]
ADDITIONAL_TRACE_COUNT = len(ADDITIONAL_TRACE_LATENCIES_MS)
DEFAULT_CRONJOB_SCHEDULE = "0 2 * * *"
DEFAULT_CRONJOB_TIME_ZONE = "Etc/UTC"
TEMPORARY_CRONJOB_TIME_ZONE = "Etc/UTC"
TEMPORARY_SCHEDULE_DELAY_SECONDS = 120
# Rollups process only UTC days older than their 24-hour inactivity cutoff.
BACKDATE_DAYS = 2
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
    first_trace_start_ns: int,
    latencies_ms: list[int],
    index_offset: int,
    batch_description: str,
) -> list[str]:
    """Seed one batch of traces and wait until all are searchable."""
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
            start_time_ns=first_trace_start_ns + index * NS_PER_MS,
            latency_ms=latency_ms,
        )
        for index, latency_ms in enumerate(latencies_ms)
    ]
    missing = _wait_for_seeded_traces(
        admin_client=test_context.user_client,
        experiment_id=experiment_id,
        trace_ids=trace_ids,
    )
    if missing:
        pytest.fail(
            f"{batch_description} traces did not become searchable: {sorted(missing)}"
        )
    return trace_ids


def _expected_rollup_values(latencies_ms: list[int]) -> dict[str, int]:
    return {
        "trace_count": len(latencies_ms),
        "latency_sum": sum(latencies_ms),
        "latency_max": max(latencies_ms),
        "latency_min": min(latencies_ms),
    }


@wait_until(
    description="seeded traces to become searchable",
    timeout=TRACE_VISIBILITY_TIMEOUT_SECONDS,
    interval=POLL_INTERVAL_SECONDS,
    until=lambda missing: not missing,
)
def _wait_for_seeded_traces(admin_client: Any, experiment_id: str, trace_ids: list[str]) -> set[str]:
    """Return trace IDs whose metadata is not searchable yet."""
    expected = set(trace_ids)
    traces = admin_client.search_traces(
        experiment_ids=[experiment_id], max_results=100, include_spans=False
    )
    visible = {trace.info.trace_id for trace in traces}
    return expected - visible


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


def _backend_secret_reference(env_vars: list[Any] | None) -> str | None:
    for env_var in env_vars or []:
        name = env_var.get("name") if isinstance(env_var, dict) else env_var.name
        value_from = (
            env_var.get("valueFrom") if isinstance(env_var, dict) else env_var.value_from
        )
        if not value_from:
            continue
        secret_ref = (
            value_from.get("secretKeyRef")
            if isinstance(value_from, dict)
            else value_from.secret_key_ref
        )
        if not secret_ref:
            continue
        secret_name = (
            secret_ref.get("name") if isinstance(secret_ref, dict) else secret_ref.name
        )
        if name == BACKEND_STORE_ENV_NAME and secret_name:
            return secret_name
    return None


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
    )
    if isinstance(response, str):
        return response.strip()
    return "".join(response).strip()


def _mlflow_cr(custom_api: client.CustomObjectsApi) -> dict[str, Any]:
    return custom_api.get_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name="mlflow",
    )


def _rollup_backend_secret_reference(batch_api: client.BatchV1Api) -> str | None:
    cronjob = batch_api.read_namespaced_cron_job(
        name=ROLLUPS_CRONJOB_NAME, namespace=Config.MLFLOW_NAMESPACE
    )
    for container in cronjob.spec.job_template.spec.template.spec.containers:
        if container.name == ROLLUPS_CONTAINER_NAME:
            return _backend_secret_reference(env_vars=container.env)
    pytest.fail(f"CronJob {ROLLUPS_CRONJOB_NAME!r} has no rollup container")


@wait_until(
    description="rollup CronJob schedule to be reconciled",
    timeout=CRONJOB_RECONCILE_TIMEOUT_SECONDS,
    interval=POLL_INTERVAL_SECONDS,
    until=lambda schedule: schedule is not None,
    retry_rules={ApiException: None},
)
def _wait_for_rollup_schedule(
    batch_api: client.BatchV1Api,
    expected_schedule: str,
    expected_time_zone: str,
) -> str | None:
    cronjob = batch_api.read_namespaced_cron_job(
        name=ROLLUPS_CRONJOB_NAME, namespace=Config.MLFLOW_NAMESPACE
    )
    if (
        cronjob.spec.schedule != expected_schedule
        or cronjob.spec.time_zone != expected_time_zone
    ):
        return None
    return cronjob.spec.schedule


def _cronjob_jobs(
    batch_api: client.BatchV1Api,
    cronjob_uid: str | None,
    created_after: datetime,
) -> list[client.V1Job]:
    return [
        job
        for job in batch_api.list_namespaced_job(
            namespace=Config.MLFLOW_NAMESPACE
        ).items
        if job.metadata.creation_timestamp
        and job.metadata.creation_timestamp >= created_after
        and any(
            owner_reference.controller is True
            and owner_reference.kind == "CronJob"
            and owner_reference.name == ROLLUPS_CRONJOB_NAME
            and owner_reference.uid == cronjob_uid
            for owner_reference in job.metadata.owner_references or []
        )
    ]


def _wait_for_cronjob_job(
    batch_api: client.BatchV1Api,
    cronjob_uid: str | None,
    created_after: datetime,
    expected_schedule: str,
    expected_time_zone: str,
    deadline: float,
) -> str:
    last_error: ApiException | None = None
    last_observed_schedule: str | None = None
    last_observed_time_zone: str | None = None
    last_observed_jobs: list[str] = []
    while time.monotonic() < deadline:
        try:
            cronjob = batch_api.read_namespaced_cron_job(
                name=ROLLUPS_CRONJOB_NAME, namespace=Config.MLFLOW_NAMESPACE
            )
            last_observed_schedule = cronjob.spec.schedule
            last_observed_time_zone = cronjob.spec.time_zone
            schedule_applied = (
                last_observed_schedule == expected_schedule
                and last_observed_time_zone == expected_time_zone
            )
            if schedule_applied:
                jobs = _cronjob_jobs(
                    batch_api=batch_api,
                    cronjob_uid=cronjob_uid,
                    created_after=created_after,
                )
                last_observed_jobs = [
                    job.metadata.name for job in jobs if job.metadata.name
                ]
                for job in jobs:
                    if job.metadata.name:
                        return job.metadata.name
        except ApiException as exc:
            if exc.status not in {404, 429} and (exc.status or 0) < 500:
                raise
            last_error = exc

        time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))

    raise WaitTimeoutError(
        f"Timed out after {CRONJOB_START_TIMEOUT_SECONDS}s waiting for the CronJob "
        f"schedule {expected_schedule!r} in {expected_time_zone!r} to create a Job; "
        f"last observed schedule={last_observed_schedule!r}, "
        f"timezone={last_observed_time_zone!r}, jobs={last_observed_jobs!r}, "
        f"last API error={last_error!r}"
    ) from last_error


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


def _one_time_rollup_schedule() -> tuple[str, datetime]:
    """Return a UTC Cron expression near two minutes from now."""
    earliest_run = datetime.now(timezone.utc) + timedelta(
        seconds=TEMPORARY_SCHEDULE_DELAY_SECONDS
    )
    # CronJob schedules have minute precision. Round the requested two-minute
    # delay to the nearest minute so the three-minute wait retains controller slack.
    scheduled_at = (earliest_run + timedelta(seconds=30)).replace(
        second=0, microsecond=0
    )
    # Exact minute/hour/day/month fields give one occurrence in the test window;
    # test cleanup restores the original schedule before the annual recurrence.
    schedule = (
        f"{scheduled_at.minute} {scheduled_at.hour} "
        f"{scheduled_at.day} {scheduled_at.month} *"
    )
    return schedule, scheduled_at


def _register_rollup_job_for_cleanup(
    test_context: TestContext, job_name: str
) -> None:
    state = test_context.trace_rollups_state
    test_created_jobs = state.setdefault("test_created_job_names", [])
    if job_name not in test_created_jobs:
        test_created_jobs.append(job_name)
        test_context.add_job_for_cleanup(job_name, state["namespace"])


def _schedule_rollup_job(test_context: TestContext) -> str:
    """Set a one-occurrence schedule and wait up to three minutes for its Job."""
    state = test_context.trace_rollups_state
    custom_api = state["custom_api"]
    schedule, scheduled_at = _one_time_rollup_schedule()
    cr_patch_started_at = datetime.now(timezone.utc)

    if "original_trace_rollups_spec" not in state:
        original_cr = _mlflow_cr(custom_api=custom_api)
        state["original_trace_rollups_spec"] = deepcopy(
            original_cr.get("spec", {}).get("traceRollups")
        )
    state["temporary_schedule_window"] = {
        "schedule": schedule,
        "scheduled_at": scheduled_at,
    }

    logger.info(
        "Setting %s to one UTC occurrence at %s (schedule %r); waiting up to %ss "
        "for the operator and CronJob controller",
        ROLLUPS_CRONJOB_NAME,
        scheduled_at.isoformat(),
        schedule,
        CRONJOB_START_TIMEOUT_SECONDS,
    )
    deadline = time.monotonic() + CRONJOB_START_TIMEOUT_SECONDS
    custom_api.patch_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name="mlflow",
        body={
            "spec": {
                "traceRollups": {
                    "schedule": schedule,
                    "timeZone": TEMPORARY_CRONJOB_TIME_ZONE,
                }
            }
        },
    )
    try:
        job_name = _wait_for_cronjob_job(
            batch_api=state["batch_api"],
            cronjob_uid=state["cronjob_uid"],
            created_after=scheduled_at,
            expected_schedule=schedule,
            expected_time_zone=TEMPORARY_CRONJOB_TIME_ZONE,
            deadline=deadline,
        )
    except WaitTimeoutError as exc:
        pytest.fail(
            f"CronJob did not apply the one-occurrence schedule and create a Job "
            f"within {CRONJOB_START_TIMEOUT_SECONDS}s; schedule patch began at "
            f"{cr_patch_started_at.isoformat()}:\n{exc}"
        )

    _register_rollup_job_for_cleanup(test_context, job_name)
    logger.info("CronJob created scheduled rollup Job %s", job_name)
    return job_name


def _restore_rollup_schedule(test_context: TestContext) -> None:
    """Restore the CR's original rollup settings during test resource cleanup."""
    state = test_context.trace_rollups_state
    if "original_trace_rollups_spec" not in state:
        return

    original_spec = deepcopy(state["original_trace_rollups_spec"])
    expected_schedule = (
        (original_spec or {}).get("schedule") or DEFAULT_CRONJOB_SCHEDULE
    )
    expected_time_zone = (
        (original_spec or {}).get("timeZone") or DEFAULT_CRONJOB_TIME_ZONE
    )
    logger.info(
        "Restoring %s schedule to %s in %s during test cleanup",
        ROLLUPS_CRONJOB_NAME,
        expected_schedule,
        expected_time_zone,
    )
    restored_spec = None
    if original_spec is not None:
        restored_spec = {
            **original_spec,
            "schedule": original_spec.get("schedule"),
            "timeZone": original_spec.get("timeZone"),
        }
    state["custom_api"].patch_cluster_custom_object(
        group=MLFLOW_API_GROUP,
        version=MLFLOW_API_VERSION,
        plural=MLFLOW_API_PLURAL,
        name="mlflow",
        body={"spec": {"traceRollups": restored_spec}},
    )
    _wait_for_rollup_schedule(
        batch_api=state["batch_api"],
        expected_schedule=expected_schedule,
        expected_time_zone=expected_time_zone,
    )
    logger.info("Restored the original rollup CronJob schedule and timezone")


def _register_active_temporary_rollup_jobs_for_cleanup(
    test_context: TestContext,
) -> None:
    """Track late Jobs from the current temporary schedule before restoring it."""
    state = test_context.trace_rollups_state
    window = state.get("temporary_schedule_window")
    if not window:
        return

    cronjob = state["batch_api"].read_namespaced_cron_job(
        name=ROLLUPS_CRONJOB_NAME,
        namespace=state["namespace"],
    )
    if (
        cronjob.spec.schedule != window["schedule"]
        or cronjob.spec.time_zone != TEMPORARY_CRONJOB_TIME_ZONE
    ):
        logger.info(
            "Skipping late Job discovery because %s no longer has the test schedule",
            ROLLUPS_CRONJOB_NAME,
        )
        return

    for job in _cronjob_jobs(
        batch_api=state["batch_api"],
        cronjob_uid=state["cronjob_uid"],
        created_after=window["scheduled_at"],
    ):
        if job.metadata.name:
            _register_rollup_job_for_cleanup(test_context, job.metadata.name)


def _wait_for_rollup_job(test_context: TestContext, job_name: str) -> None:
    state = test_context.trace_rollups_state
    wait_for_job_complete(
        state["batch_api"],
        state["core_api"],
        job_name,
        state["namespace"],
        container_name=ROLLUPS_CONTAINER_NAME,
        job_description="SQL trace rollup",
    )


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
        "apps_api": apps_api,
        "custom_api": custom_api,
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
        or _backend_secret_reference(tracking_container.env) != DB_CREDENTIALS_SECRET_NAME
        or replicas < 1
        or deployment_status.observed_generation < deployment.metadata.generation
        or (deployment_status.updated_replicas or 0) != replicas
        or (deployment_status.ready_replicas or 0) != replicas
    ):
        pytest.fail("Tracking Deployment is not ready on the harness backend Secret")

    cronjob = batch_api.read_namespaced_cron_job(
        name=ROLLUPS_CRONJOB_NAME, namespace=namespace
    )
    if _rollup_backend_secret_reference(batch_api=batch_api) != DB_CREDENTIALS_SECRET_NAME:
        pytest.fail("Trace-rollup CronJob does not use the harness backend Secret")
    if not cronjob.metadata.uid:
        pytest.fail("Trace-rollup CronJob has no UID")

    rollups_spec = spec.get("traceRollups") or {}
    expected_schedule = rollups_spec.get("schedule") or DEFAULT_CRONJOB_SCHEDULE
    expected_time_zone = rollups_spec.get("timeZone") or DEFAULT_CRONJOB_TIME_ZONE
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
            "cronjob_uid": cronjob.metadata.uid,
            "expected_schedule": expected_schedule,
            "expected_time_zone": expected_time_zone,
            "cronjob_schedule": cronjob.spec.schedule,
            "cronjob_time_zone": cronjob.spec.time_zone,
            "rollup_passes": 0,
            "rollup_job_names": [],
            "test_created_job_names": [],
            "expected_rollup_passes": [],
            "sql_rollup_passes": [],
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
    """Create and wait for five traces with known latencies and old timestamps."""
    state = test_context.trace_rollups_state
    workspace = test_context.active_workspace
    first_trace_start_ns = time.time_ns() - BACKDATE_DAYS * MS_PER_DAY * NS_PER_MS
    trace_ids = _seed_trace_batch(
        test_context,
        first_trace_start_ns=first_trace_start_ns,
        latencies_ms=EXPECTED_TRACE_LATENCIES_MS,
        index_offset=0,
        batch_description="Seeded",
    )
    state.update(
        {
            "workspace": workspace,
            "experiment_id": test_context.active_experiment_id,
            "trace_ids": trace_ids,
            "trace_latencies_ms": list(EXPECTED_TRACE_LATENCIES_MS),
            "traces_ready_at": datetime.now(timezone.utc),
            "start_time_ms": first_trace_start_ns // NS_PER_MS // MS_PER_DAY * MS_PER_DAY,
        }
    )
    state["end_time_ms"] = state["start_time_ms"] + MS_PER_DAY
    state["expected"] = _expected_rollup_values(state["trace_latencies_ms"])
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

    # Reuse the first batch's UTC day so the next Job must refresh an existing
    # daily partition while including the newly seeded traces.
    additional_start_time_ms = state["start_time_ms"] + len(existing_trace_ids) + 1
    first_trace_start_ns = additional_start_time_ms * NS_PER_MS
    additional_trace_ids = _seed_trace_batch(
        test_context,
        first_trace_start_ns=first_trace_start_ns,
        latencies_ms=ADDITIONAL_TRACE_LATENCIES_MS,
        index_offset=len(existing_trace_ids),
        batch_description="Additional",
    )

    state["additional_trace_ids"] = additional_trace_ids
    state["trace_ids_before_additional_seed"] = list(existing_trace_ids)
    state["additional_start_time_ms"] = additional_start_time_ms
    state["additional_traces_ready_at"] = datetime.now(timezone.utc)
    state["trace_ids"].extend(additional_trace_ids)
    state["trace_latencies_ms"].extend(ADDITIONAL_TRACE_LATENCIES_MS)
    state["expected"] = _expected_rollup_values(state["trace_latencies_ms"])
    state["expected_rollup_passes"].append(dict(state["expected"]))
    logger.info(
        "Seeded %s additional traces in the existing UTC rollup day; "
        "combined trace count is %s; trace IDs: %s",
        len(additional_trace_ids),
        len(state["trace_ids"]),
        additional_trace_ids,
    )


def _run_trace_rollup_job(test_context: TestContext, *, force_new_job: bool) -> None:
    """Wait for an existing post-seed Job or make the CronJob create a new one."""
    state = test_context.trace_rollups_state
    batch_api = state["batch_api"]
    created_after = datetime.now(timezone.utc) if force_new_job else state["traces_ready_at"]
    job_name = None
    job_completed = False
    if not force_new_job:
        job_name = next(
            (
                job.metadata.name
                for job in _cronjob_jobs(
                    batch_api=batch_api,
                    cronjob_uid=state["cronjob_uid"],
                    created_after=created_after,
                )
                if job.metadata.name
            ),
            None,
        )
    if job_name:
        logger.info(
            "Found rollup Job %s created after trace seeding; waiting for completion",
            job_name,
        )
    else:
        job_name = _schedule_rollup_job(test_context)
        logger.info("Waiting for scheduled rollup Job %s to complete", job_name)
        _wait_for_rollup_job(test_context, job_name)
        job_completed = True

    pass_number = state["rollup_passes"] + 1
    state["rollup_passes"] = pass_number
    state["rollup_job_names"].append(job_name)
    logger.info(
        "Waiting for rollup pass %d Job %s to complete",
        pass_number,
        job_name,
    )
    if not job_completed:
        _wait_for_rollup_job(test_context, job_name)
    logger.info("Rollup pass %d Job %s completed", pass_number, job_name)


def action_run_trace_rollup_job(test_context: TestContext) -> None:
    """Run the first live SQL trace-rollup CronJob pass."""
    _run_trace_rollup_job(test_context, force_new_job=False)


def action_run_incremental_trace_rollup_job(test_context: TestContext) -> None:
    """Run a second, distinct Job after adding traces to the rolled-up day."""
    _run_trace_rollup_job(test_context, force_new_job=True)


def action_read_trace_rollup_rows(test_context: TestContext) -> None:
    """Read this experiment's materialized trace metrics from PostgreSQL."""
    state = test_context.trace_rollups_state
    experiment_id = int(test_context.active_experiment_id)
    sql = f"""
        SELECT COALESCE(SUM(sample_count) FILTER (WHERE metric_name = 'trace_count'), 0),
               COALESCE(SUM(sum_value) FILTER (WHERE metric_name = 'latency'), 0),
               COALESCE(MAX(max_value) FILTER (WHERE metric_name = 'latency'), 0),
               COALESCE(MIN(min_value) FILTER (WHERE metric_name = 'latency'), 0)
        FROM {ROLLUP_TABLE}
        WHERE experiment_id = {experiment_id} AND grouping_set = 'global'
    """
    observed = tuple(
        map(
            int,
            _database_query(
                core_api=state["core_api"],
                database_pod=state["database_pod"],
                db_name=state["database"],
                db_user=state["database_user"],
                sql=sql,
            ).split("|"),
        )
    )
    state["observed_sql_rollups"] = observed
    state["sql_rollup_passes"].append(observed)
    logger.info(
        "Observed SQL rollup values for experiment %s: %s",
        experiment_id,
        observed,
    )


def action_query_trace_rollup_metrics(test_context: TestContext) -> None:
    """Read public trace metrics for the seeded experiment."""
    state = test_context.trace_rollups_state
    experiment_id = test_context.active_experiment_id
    state["observed_count_metrics"] = _trace_metric_values(
        experiment_id=experiment_id,
        start_time_ms=state["start_time_ms"],
        end_time_ms=state["end_time_ms"],
        metric_name="trace_count",
        aggregations=["COUNT"],
        workspace=state["workspace"],
    )
    state["observed_latency_metrics"] = _trace_metric_values(
        experiment_id=experiment_id,
        start_time_ms=state["start_time_ms"],
        end_time_ms=state["end_time_ms"],
        metric_name="latency",
        aggregations=["AVG", "MAX", "MIN"],
        workspace=state["workspace"],
    )
    state["api_metric_passes"].append(
        (state["observed_count_metrics"], state["observed_latency_metrics"])
    )
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
    """Restore scheduling, then remove this smoke test's rows and Jobs."""
    state = test_context.trace_rollups_state
    if not state:
        return

    cleanup_errors: list[Exception] = []
    job_cleanup_failed = False
    try:
        # Discover late Jobs while the temporary schedule is still active. Once
        # the original schedule is restored, a catch-up Job could otherwise be
        # mistaken for a test-created Job and deleted below.
        _register_active_temporary_rollup_jobs_for_cleanup(test_context)
    except Exception as exc:
        cleanup_errors.append(exc)

    try:
        _restore_rollup_schedule(test_context)
    except Exception as exc:
        cleanup_errors.append(exc)

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
