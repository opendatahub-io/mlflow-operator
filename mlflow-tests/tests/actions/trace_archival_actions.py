"""Trace archival smoke action functions.

Each action accepts only test_context and updates archival_state as needed.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid

import boto3
import pytest
import requests
from botocore.config import Config as BotocoreConfig
from kubernetes import client
from kubernetes.client.rest import ApiException
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.tracing.constant import SpansLocation, TraceTagKey
from mlflow.tracing.utils.otlp import (
    MLFLOW_EXPERIMENT_ID_HEADER,
    OTLP_TRACES_PATH,
    resource_to_otel_proto,
)
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME
from mlflow_tests.utils.client import ClientManager
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from ..constants.config import Config
from ..http_utils import get_mlflow_base_uri, get_requests_verify_value
from ..shared import TestContext

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


def count_archive_objects(s3_client, bucket: str) -> int:
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=ARCHIVAL_PREFIX):
        count += len(page.get("Contents", []))
    return count


def wait_for_expected_traces(
    admin_client,
    experiment_id: str,
    expected_root_span_ids: dict[str, str],
):
    """Wait until each seeded trace contains its asynchronously exported root span."""
    expected_trace_ids = set(expected_root_span_ids)
    deadline = time.monotonic() + TRACE_VISIBILITY_TIMEOUT_SECONDS
    observed = {}
    while time.monotonic() < deadline:
        traces = admin_client.search_traces(experiment_ids=[experiment_id], max_results=100)
        observed = {}
        for trace in traces:
            if trace.info.trace_id not in expected_trace_ids or not trace.data.spans:
                continue
            expected_root_span_id = expected_root_span_ids[trace.info.trace_id]
            if not any(span.span_id == expected_root_span_id for span in trace.data.spans):
                continue
            observed[trace.info.trace_id] = trace
        if len(observed) >= len(expected_trace_ids):
            return observed
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(
        f"Expected {len(expected_trace_ids)} visible traces with root spans "
        f"{expected_root_span_ids}, found {len(observed)} after "
        f"{TRACE_VISIBILITY_TIMEOUT_SECONDS}s"
    )


def wait_for_spans_location(
    admin_client,
    experiment_id: str,
    expected_trace_ids: set[str],
    location: str,
    *,
    failure_prefix: str,
):
    deadline = time.monotonic() + TRACE_VISIBILITY_TIMEOUT_SECONDS
    last_tags: dict[str, dict] = {}
    while time.monotonic() < deadline:
        traces = admin_client.search_traces(experiment_ids=[experiment_id], max_results=100)
        observed = {}
        for trace in traces:
            if trace.info.trace_id not in expected_trace_ids:
                continue
            tags = dict(trace.info.tags or {})
            last_tags[trace.info.trace_id] = tags
            if tags.get(TraceTagKey.SPANS_LOCATION) != location:
                continue
            if not trace.data.spans:
                continue
            observed[trace.info.trace_id] = trace
        if len(observed) >= len(expected_trace_ids):
            return observed
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(
        f"{failure_prefix} (wanted {TraceTagKey.SPANS_LOCATION}={location}) "
        f"within {TRACE_VISIBILITY_TIMEOUT_SECONDS}s: {last_tags}"
    )


def _otlp_trace_urls() -> list[str]:
    """Return OTLP ingest URLs for gateway and port-forward entrypoints.

    OpenShift HTTPRoute rewrites ``/mlflow/v1`` to ``/v1``. Kind port-forward
    talks to the Service, so only the unprefixed pod path exists.
    """
    base = get_mlflow_base_uri()
    urls = [f"{base}{OTLP_TRACES_PATH}"]
    suffix = "/mlflow"
    if base.endswith(suffix):
        unprefixed = f"{base[: -len(suffix)]}{OTLP_TRACES_PATH}"
        if unprefixed not in urls:
            urls.append(unprefixed)
    return urls


def _log_spans_via_otlp(experiment_id: str, workspace: str, spans) -> None:
    """Write spans through OTLP so archival sees tracking-store span bodies.

    SDK ``RestStore.log_spans()`` concatenates ``/v1/traces`` onto
    ``MLFLOW_TRACKING_URI``. That works on OpenShift because HTTPRoute rewrites
    ``/mlflow/v1`` to ``/v1``. Kind port-forward skips HTTPRoute, so the same
    request 404s and traces stay artifact-backed. Posting the OTLP payload to
    the prefixed tracking URI first, then the unprefixed origin, covers both.
    """
    if not spans:
        raise AssertionError("OTLP log_spans requires at least one span")

    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    resource = getattr(spans[0]._span, "resource", None)
    resource_spans.resource.CopyFrom(resource_to_otel_proto(resource))
    scope_spans = resource_spans.scope_spans.add()
    scope_spans.spans.extend(span.to_otel_proto() for span in spans)

    headers = {
        "Authorization": f"Bearer {Config.K8_API_TOKEN}",
        "Content-Type": "application/x-protobuf",
        MLFLOW_EXPERIMENT_ID_HEADER: str(experiment_id),
        WORKSPACE_HEADER_NAME: workspace,
    }
    payload = request.SerializeToString()
    failures: list[str] = []
    for url in _otlp_trace_urls():
        logger.info(
            "Logging %d span(s) via OTLP %s for experiment %s workspace %s",
            len(spans),
            url,
            experiment_id,
            workspace,
        )
        response = requests.post(
            url,
            data=payload,
            headers=headers,
            verify=get_requests_verify_value(),
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code < 400:
            return
        failures.append(f"{url} -> {response.status_code} {response.text}")
        logger.warning("OTLP log_spans POST %s failed: %s", url, failures[-1])

    raise AssertionError("OTLP log_spans failed on all ingest URLs: " + "; ".join(failures))


def _create_trace_payload(admin_client, experiment_id: str, index: int) -> dict:
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
        "spans": [root_span, child_span],
    }


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


def pod_diagnostics(core_api: client.CoreV1Api, namespace: str, job_name: str) -> str:
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
                pod_diagnostics(core_api, namespace, job_name),
            )
            return
        if _condition_status(last_job, "Failed") == "True":
            pytest.fail(
                f"Trace archival Job {job_name} Failed before completion.\n"
                f"{pod_diagnostics(core_api, namespace, job_name)}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    diagnostics = pod_diagnostics(core_api, namespace, job_name)
    pytest.fail(
        f"Trace archival Job {job_name} did not Complete within "
        f"{JOB_COMPLETE_TIMEOUT_SECONDS}s.\n{diagnostics}"
    )


def action_prepare_archival_smoke(test_context: TestContext) -> None:
    """Validate harness config and capture the pre-job archive object count."""
    s3_client, bucket = _archive_s3_client()
    test_context.archival_state = {
        "namespace": _operator_namespace(),
        "job_name": f"archival-e2e-{uuid.uuid4().hex[:8]}",
        "retention_seconds": _retention_seconds(),
        "s3_client": s3_client,
        "bucket": bucket,
        "before_archive_count": count_archive_objects(s3_client, bucket),
        "expected_payloads": [],
        "expected_trace_ids": set(),
        "observed_traces": {},
        "after_archive_count": 0,
    }
    logger.info(
        "Prepared archival smoke in namespace %s against s3://%s/%s",
        test_context.archival_state["namespace"],
        bucket,
        ARCHIVAL_PREFIX,
    )


def action_seed_archival_traces(test_context: TestContext) -> None:
    """Create archival smoke traces and wait until they are visible."""
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before seeding archival traces")
    if not test_context.active_experiment_id:
        raise ValueError("test_context.active_experiment_id must be set before seeding archival traces")

    expected_payloads = [
        _create_trace_payload(test_context.user_client, test_context.active_experiment_id, index)
        for index in range(ARCHIVAL_TRACE_COUNT)
    ]
    expected_root_span_ids = {
        payload["trace_id"]: payload["spans"][0].span_id for payload in expected_payloads
    }
    expected_trace_ids = set(expected_root_span_ids)
    observed = wait_for_expected_traces(
        test_context.user_client,
        test_context.active_experiment_id,
        expected_root_span_ids,
    )
    test_context.archival_state["expected_payloads"] = expected_payloads
    test_context.archival_state["expected_trace_ids"] = expected_trace_ids
    test_context.archival_state["observed_traces"] = observed


def action_persist_archival_spans_via_otlp(test_context: TestContext) -> None:
    """Persist seeded spans through OTLP so the scheduler sees tracking-store bodies."""
    if not test_context.active_experiment_id:
        raise ValueError("test_context.active_experiment_id must be set before OTLP log_spans")
    if not test_context.active_workspace:
        raise ValueError("test_context.active_workspace must be set before OTLP log_spans")
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before OTLP log_spans")

    for payload in test_context.archival_state["expected_payloads"]:
        _log_spans_via_otlp(
            test_context.active_experiment_id,
            test_context.active_workspace,
            payload["spans"],
        )
    test_context.archival_state["observed_traces"] = wait_for_spans_location(
        test_context.user_client,
        test_context.active_experiment_id,
        test_context.archival_state["expected_trace_ids"],
        SpansLocation.TRACKING_STORE.value,
        failure_prefix="OTLP log_spans completed but traces were not DB-backed",
    )


def action_wait_for_archival_retention(test_context: TestContext) -> None:
    """Wait past the harness retention so seeded traces become archivable."""
    wait_seconds = (
        test_context.archival_state["retention_seconds"] + RETENTION_WAIT_BUFFER_SECONDS
    )
    logger.info(
        "Waiting %ds for traces to age past archival retention %s",
        wait_seconds,
        Config.TRACE_ARCHIVAL_RETENTION,
    )
    time.sleep(wait_seconds)


def action_run_archival_job_from_cronjob(test_context: TestContext) -> None:
    """Create a Job from the operator CronJob template and wait for completion."""
    namespace = test_context.archival_state["namespace"]
    job_name = test_context.archival_state["job_name"]
    ClientManager.load_k8s_config()
    batch_api = client.BatchV1Api()
    core_api = client.CoreV1Api()
    cronjob = _wait_for_cronjob(batch_api, namespace)
    batch_api.create_namespaced_job(namespace, _job_from_cronjob(cronjob, job_name, namespace))
    test_context.add_job_for_cleanup(job_name, namespace)
    _wait_for_job_complete(batch_api, core_api, job_name, namespace)


def action_wait_for_archive_objects(test_context: TestContext) -> None:
    """Wait until new archive objects appear under the configured S3 prefix."""
    s3_client = test_context.archival_state["s3_client"]
    bucket = test_context.archival_state["bucket"]
    before_count = test_context.archival_state["before_archive_count"]
    deadline = time.monotonic() + ARCHIVE_OBJECT_WAIT_TIMEOUT_SECONDS
    after_count = before_count
    while time.monotonic() < deadline:
        after_count = count_archive_objects(s3_client, bucket)
        if after_count > before_count:
            logger.info(
                "Archive object count increased from %d to %d under prefix %s",
                before_count,
                after_count,
                ARCHIVAL_PREFIX,
            )
            test_context.archival_state["after_archive_count"] = after_count
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    after_count = count_archive_objects(s3_client, bucket)
    test_context.archival_state["after_archive_count"] = after_count
    pytest.fail(
        "Trace archival Job completed but no new archive objects appeared under "
        f"s3://{bucket}/{ARCHIVAL_PREFIX} within {ARCHIVE_OBJECT_WAIT_TIMEOUT_SECONDS}s "
        f"(before={before_count}, after={after_count})"
    )


def action_reload_archival_traces(test_context: TestContext) -> None:
    """Reload seeded traces after archival so later validations can inspect them."""
    if test_context.user_client is None:
        raise ValueError("test_context.user_client must be set before reloading archival traces")
    if not test_context.active_experiment_id:
        raise ValueError("test_context.active_experiment_id must be set before reloading archival traces")

    test_context.archival_state["observed_traces"] = wait_for_spans_location(
        test_context.user_client,
        test_context.active_experiment_id,
        test_context.archival_state["expected_trace_ids"],
        SpansLocation.ARCHIVE_REPO.value,
        failure_prefix="Archival Job completed but traces were not archive-backed",
    )
