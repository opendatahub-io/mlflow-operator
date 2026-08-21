"""Live trace-archival smoke coverage against object storage.

Creates several real traces, waits them past the harness-configured archival
retention, runs a Job from the operator-managed CronJob template, then verifies
that archive objects were written while the traces remain readable via MLflow.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid

import boto3
import mlflow
import pytest
from botocore.config import Config as BotocoreConfig
from kubernetes import client
from kubernetes.client.rest import ApiException
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID

from mlflow_tests.utils.client import ClientManager

from .constants.config import Config

logger = logging.getLogger(__name__)

ARCHIVAL_CRONJOB_NAME = "mlflow-trace-archival"
ARCHIVAL_CONTAINER_NAME = "mlflow-trace-archival"
ARCHIVAL_PREFIX = "trace-archive"
ARCHIVAL_TRACE_COUNT = 3
CRONJOB_WAIT_TIMEOUT_SECONDS = 120
JOB_COMPLETE_TIMEOUT_SECONDS = 180
ARCHIVE_OBJECT_WAIT_TIMEOUT_SECONDS = 30
RETENTION_WAIT_BUFFER_SECONDS = 10
TRACE_VISIBILITY_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 2


def _operator_namespace() -> str:
    return os.getenv("NAMESPACE", "opendatahub")


def _retention_seconds() -> int:
    raw = (Config.TRACE_ARCHIVAL_RETENTION or "").strip()
    match = re.fullmatch(r"(\d+)([mhd])", raw)
    if match is None:
        pytest.skip(
            "trace archival semantic smoke requires a harness-provided "
            f"TRACE_ARCHIVAL_RETENTION using the x[mhd] grammar, got {raw!r}"
        )

    value = int(match.group(1))
    unit = match.group(2)
    multiplier = {"m": 60, "h": 3600, "d": 86400}[unit]
    seconds = value * multiplier
    if seconds > 300:
        pytest.skip(
            "trace archival semantic smoke only runs with a short retention "
            f"(<= 5m); got {Config.TRACE_ARCHIVAL_RETENTION!r}"
        )
    return seconds


def _archive_s3_client():
    bucket = (Config.S3_BUCKET or "").strip()
    access_key = (Config.AWS_ACCESS_KEY or "").strip()
    secret_key = (Config.AWS_SECRET_KEY or "").strip()
    if not bucket or not access_key or not secret_key:
        pytest.skip(
            "trace archival semantic smoke requires AWS_S3_BUCKET, "
            "AWS_ACCESS_KEY_ID, and AWS_SECRET_ACCESS_KEY"
        )

    endpoint_url = (Config.S3_URL or "").strip() or None
    boto_kwargs = {
        "service_name": "s3",
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if endpoint_url is not None:
        boto_kwargs["endpoint_url"] = endpoint_url
        boto_kwargs["config"] = BotocoreConfig(s3={"addressing_style": "path"})
        # The SeaweedFS TLS test endpoint is port-forwarded to localhost, but the
        # generated cert SANs only cover the in-cluster service DNS names.
        if endpoint_url.startswith("https://localhost:") and Config.DISABLE_TLS == "true":
            boto_kwargs["verify"] = False

    return boto3.client(**boto_kwargs), bucket


def _count_archive_objects(s3_client, bucket: str) -> int:
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=ARCHIVAL_PREFIX):
        count += len(page.get("Contents", []))
    return count


def _wait_for_archive_objects(s3_client, bucket: str, before_count: int) -> int:
    deadline = time.monotonic() + ARCHIVE_OBJECT_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        after_count = _count_archive_objects(s3_client, bucket)
        if after_count > before_count:
            logger.info(
                "Archive object count increased from %d to %d under prefix %s",
                before_count,
                after_count,
                ARCHIVAL_PREFIX,
            )
            return after_count
        time.sleep(POLL_INTERVAL_SECONDS)

    after_count = _count_archive_objects(s3_client, bucket)
    pytest.fail(
        "Trace archival Job completed but no new archive objects appeared under "
        f"s3://{bucket}/{ARCHIVAL_PREFIX} within {ARCHIVE_OBJECT_WAIT_TIMEOUT_SECONDS}s "
        f"(before={before_count}, after={after_count})"
    )


def _wait_for_cronjob(batch_api: client.BatchV1Api, namespace: str) -> client.V1CronJob:
    deadline = time.monotonic() + CRONJOB_WAIT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            cronjob = batch_api.read_namespaced_cron_job(ARCHIVAL_CRONJOB_NAME, namespace)
            logger.info("Found CronJob %s in namespace %s", ARCHIVAL_CRONJOB_NAME, namespace)
            return cronjob
        except ApiException as exc:
            if exc.status != 404:
                raise
            last_error = exc
            logger.debug("Waiting for CronJob %s: %s", ARCHIVAL_CRONJOB_NAME, exc.reason)
            time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"CronJob {ARCHIVAL_CRONJOB_NAME} was not created in namespace {namespace} "
        f"within {CRONJOB_WAIT_TIMEOUT_SECONDS}s: {last_error}"
    )


def _job_from_cronjob(cronjob: client.V1CronJob, job_name: str, namespace: str) -> client.V1Job:
    template = cronjob.spec.job_template
    annotations = {}
    labels = {"created-by": "mlflow-tests"}
    if template.metadata is not None:
        if template.metadata.annotations:
            annotations.update(template.metadata.annotations)
        if template.metadata.labels:
            labels.update(template.metadata.labels)
    annotations["cronjob.kubernetes.io/instantiate"] = "manual"
    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=labels,
            annotations=annotations,
        ),
        spec=template.spec,
    )


def _condition_status(job: client.V1Job, condition_type: str) -> str | None:
    for condition in job.status.conditions or []:
        if condition.type == condition_type:
            return condition.status
    return None


def _pod_diagnostics(core_api: client.CoreV1Api, namespace: str, job_name: str) -> str:
    lines = [f"Job {job_name} diagnostics in namespace {namespace}:"]
    try:
        pods = core_api.list_namespaced_pod(namespace, label_selector=f"job-name={job_name}")
    except ApiException as exc:
        return f"{lines[0]} failed to list pods: {exc}"

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
                    f"  container {status.name} waiting reason={waiting.reason} "
                    f"message={waiting.message}"
                )
            if terminated is not None:
                lines.append(
                    f"  container {status.name} terminated reason={terminated.reason} "
                    f"exit_code={terminated.exit_code} message={terminated.message}"
                )
        try:
            events = core_api.list_namespaced_event(
                namespace,
                field_selector=f"involvedObject.name={pod.metadata.name}",
            )
            for event in events.items:
                lines.append(
                    f"  event type={event.type} reason={event.reason} message={event.message}"
                )
        except Exception as exc:
            lines.append(f"  failed to list events: {exc}")
        try:
            logs = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=ARCHIVAL_CONTAINER_NAME,
                tail_lines=200,
            )
            lines.append(f"  logs:\n{logs}")
        except ApiException as exc:
            lines.append(f"  logs unavailable: {exc.reason}")
    return "\n".join(lines)


def _wait_for_job_complete(
    batch_api: client.BatchV1Api,
    core_api: client.CoreV1Api,
    job_name: str,
    namespace: str,
) -> None:
    deadline = time.monotonic() + JOB_COMPLETE_TIMEOUT_SECONDS
    last_job: client.V1Job | None = None
    while time.monotonic() < deadline:
        try:
            last_job = batch_api.read_namespaced_job_status(job_name, namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if _condition_status(last_job, "Complete") == "True":
            logger.info("Job %s completed successfully", job_name)
            logger.info(
                "Completed archival job diagnostics:\n%s",
                _pod_diagnostics(core_api, namespace, job_name),
            )
            return
        if _condition_status(last_job, "Failed") == "True":
            pytest.fail(
                f"Trace archival Job {job_name} Failed before completion.\n"
                f"{_pod_diagnostics(core_api, namespace, job_name)}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    diagnostics = _pod_diagnostics(core_api, namespace, job_name)
    pytest.fail(
        f"Trace archival Job {job_name} did not Complete within "
        f"{JOB_COMPLETE_TIMEOUT_SECONDS}s.\n{diagnostics}"
    )


def _delete_job(batch_api: client.BatchV1Api, job_name: str, namespace: str) -> None:
    try:
        batch_api.delete_namespaced_job(
            name=job_name,
            namespace=namespace,
            propagation_policy="Foreground",
        )
        logger.info("Deleted Job %s", job_name)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete Job %s: %s", job_name, exc)


def _create_trace_payload(admin_client, experiment_id: str, index: int) -> dict[str, str]:
    trace_name = f"trace-archival-smoke-{index}"
    child_span_name = f"{trace_name}-db-backed"
    message = f"trace archival smoke message {index}"
    result = f"trace archival smoke result {index}"
    root_span = admin_client.start_trace(
        name=trace_name,
        inputs={"message": message},
        tags={"mlflow.trace.user": "trace-archival-smoke"},
        experiment_id=experiment_id,
    )
    trace_id = getattr(root_span, "request_id", None) or getattr(root_span, "trace_id", None)
    if trace_id is None:
        raise AssertionError(f"Trace '{trace_name}' did not return a trace/request id")
    if trace_id == NO_OP_SPAN_TRACE_ID:
        raise AssertionError(f"Trace '{trace_name}' returned a no-op span trace id")
    child_span = admin_client.start_span(
        name=child_span_name,
        trace_id=trace_id,
        parent_id=root_span.span_id,
        inputs={"message": message},
    )
    admin_client.end_span(
        trace_id=trace_id,
        span_id=child_span.span_id,
        outputs={"result": result},
        status="OK",
    )
    admin_client.end_trace(trace_id=trace_id, outputs={"result": result}, status="OK")
    return {
        "trace_id": trace_id,
        "trace_name": trace_name,
        "message": message,
        "result": result,
    }


def _wait_for_expected_traces(admin_client, experiment_id: str, expected_trace_ids: set[str]):
    deadline = time.monotonic() + TRACE_VISIBILITY_TIMEOUT_SECONDS
    observed = {}
    while time.monotonic() < deadline:
        traces = admin_client.search_traces(experiment_ids=[experiment_id], max_results=100)
        observed = {}
        for trace in traces:
            if trace.info.trace_id not in expected_trace_ids or not trace.data.spans:
                continue
            observed[trace.info.trace_id] = trace
        if len(observed) >= len(expected_trace_ids):
            return observed
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(
        f"Expected {len(expected_trace_ids)} visible traces for {sorted(expected_trace_ids)}, "
        f"found {len(observed)} after {TRACE_VISIBILITY_TIMEOUT_SECONDS}s"
    )


def _assert_trace_payloads(observed_traces, expected_payloads: list[dict[str, str]]) -> None:
    for payload in expected_payloads:
        trace = observed_traces.get(payload["trace_id"])
        assert trace is not None, f"Trace {payload['trace_id']} was not found after archival"
        assert trace.data.spans, f"Trace {payload['trace_id']} has no persisted spans"
        root_span = trace.data.spans[0]
        assert root_span.name == payload["trace_name"], (
            f"Expected root span '{payload['trace_name']}', got '{root_span.name}'"
        )
        assert root_span.inputs.get("message") == payload["message"], (
            f"Expected input message '{payload['message']}', got '{root_span.inputs.get('message')}'"
        )
        assert root_span.outputs.get("result") == payload["result"], (
            f"Expected output result '{payload['result']}', got '{root_span.outputs.get('result')}'"
        )


def _log_trace_diagnostics(label: str, observed_traces, expected_payloads: list[dict[str, str]]) -> None:
    now_millis = int(time.time() * 1000)
    for payload in expected_payloads:
        trace = observed_traces.get(payload["trace_id"])
        if trace is None:
            logger.info("%s trace %s is not visible yet", label, payload["trace_id"])
            continue

        span_names = [span.name for span in trace.data.spans]
        logger.info(
            "%s trace %s state=%s experiment_id=%s request_time=%s age_ms=%s "
            "span_count=%d span_names=%s tags=%s trace_metadata=%s",
            label,
            trace.info.trace_id,
            trace.info.state,
            trace.info.experiment_id,
            trace.info.request_time,
            now_millis - trace.info.request_time,
            len(trace.data.spans),
            span_names,
            trace.info.tags,
            trace.info.trace_metadata,
        )


@pytest.mark.smoke
@pytest.mark.skipif(
    Config.ARTIFACT_STORAGE != "s3",
    reason="trace archival live Job requires object storage (artifact_storage=s3)",
)
def test_trace_archival_job_archives_multiple_traces(setup_clients) -> None:
    admin_client, _k8_manager, _user_manager, workspaces = setup_clients
    # Other tests mutate process-global MLflow auth env, so re-pin fluent calls
    # to the admin token before creating archival smoke resources.
    admin_client = ClientManager.create_mlflow_client(
        token=Config.K8_API_TOKEN,
        tracking_uri=Config.MLFLOW_URI,
    )
    namespace = _operator_namespace()
    job_name = f"archival-e2e-{uuid.uuid4().hex[:8]}"
    workspace = workspaces[0]
    retention_seconds = _retention_seconds()
    s3_client, bucket = _archive_s3_client()
    before_archive_count = _count_archive_objects(s3_client, bucket)
    experiment_id: str | None = None

    ClientManager.load_k8s_config()
    batch_api = client.BatchV1Api()
    core_api = client.CoreV1Api()

    logger.info(
        "Creating %d traces in workspace %s, then running archival Job %s from CronJob %s",
        ARCHIVAL_TRACE_COUNT,
        workspace,
        job_name,
        ARCHIVAL_CRONJOB_NAME,
    )
    try:
        mlflow.set_workspace(workspace)
        experiment_name = f"trace-archival-smoke-{uuid.uuid4().hex[:8]}"
        experiment_id = mlflow.create_experiment(experiment_name)

        expected_payloads = [
            _create_trace_payload(admin_client, experiment_id, index)
            for index in range(ARCHIVAL_TRACE_COUNT)
        ]
        expected_trace_ids = {payload["trace_id"] for payload in expected_payloads}

        observed_before = _wait_for_expected_traces(
            admin_client, experiment_id, expected_trace_ids
        )
        _assert_trace_payloads(observed_before, expected_payloads)
        _log_trace_diagnostics("Pre-archival", observed_before, expected_payloads)

        wait_seconds = retention_seconds + RETENTION_WAIT_BUFFER_SECONDS
        logger.info(
            "Waiting %ds for traces to age past archival retention %s",
            wait_seconds,
            Config.TRACE_ARCHIVAL_RETENTION,
        )
        time.sleep(wait_seconds)

        cronjob = _wait_for_cronjob(batch_api, namespace)
        batch_api.create_namespaced_job(namespace, _job_from_cronjob(cronjob, job_name, namespace))
        _wait_for_job_complete(batch_api, core_api, job_name, namespace)
        _wait_for_archive_objects(s3_client, bucket, before_archive_count)

        observed_after = _wait_for_expected_traces(
            admin_client, experiment_id, expected_trace_ids
        )
        _assert_trace_payloads(observed_after, expected_payloads)
        _log_trace_diagnostics("Post-archival", observed_after, expected_payloads)
    finally:
        _delete_job(batch_api, job_name, namespace)
        if experiment_id is not None:
            try:
                mlflow.set_workspace(workspace)
                admin_client.delete_experiment(experiment_id)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                logger.warning("Failed to delete archival smoke experiment %s: %s", experiment_id, exc)
