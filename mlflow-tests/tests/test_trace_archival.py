"""Live trace-archival smoke coverage against object storage.

Creates a Job from the operator-managed CronJob template instead of waiting
for a cron tick. Skipped unless artifact storage is S3-compatible.
"""

import logging
import os
import time
import uuid

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from mlflow_tests.utils.client import ClientManager

from .constants.config import Config

logger = logging.getLogger(__name__)

ARCHIVAL_CRONJOB_NAME = "mlflow-trace-archival"
ARCHIVAL_CONTAINER_NAME = "mlflow-trace-archival"
CRONJOB_WAIT_TIMEOUT_SECONDS = 120
JOB_COMPLETE_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2


def _operator_namespace() -> str:
    return os.getenv("NAMESPACE", "opendatahub")


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


@pytest.mark.smoke
@pytest.mark.skipif(
    Config.ARTIFACT_STORAGE != "s3",
    reason="trace archival live Job requires object storage (artifact_storage=s3)",
)
def test_trace_archival_job_from_cronjob_completes() -> None:
    namespace = _operator_namespace()
    job_name = f"archival-e2e-{uuid.uuid4().hex[:8]}"

    ClientManager.load_k8s_config()
    batch_api = client.BatchV1Api()
    core_api = client.CoreV1Api()

    logger.info(
        "Creating archival Job %s from CronJob %s in namespace %s",
        job_name,
        ARCHIVAL_CRONJOB_NAME,
        namespace,
    )
    try:
        cronjob = _wait_for_cronjob(batch_api, namespace)
        batch_api.create_namespaced_job(namespace, _job_from_cronjob(cronjob, job_name, namespace))
        _wait_for_job_complete(batch_api, core_api, job_name, namespace)
    finally:
        _delete_job(batch_api, job_name, namespace)
