"""Shared diagnostics and polling helpers for Jobs based on CronJob templates."""

from __future__ import annotations

import logging

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException
from urllib3.exceptions import HTTPError

from mlflow_tests.utils.client import read_kubernetes_response
from mlflow_tests.utils.wait import WaitTimeoutError, wait_until

logger = logging.getLogger(__name__)
JOB_API_REQUEST_TIMEOUT_SECONDS = 10
JOB_DIAGNOSTICS_REQUEST_TIMEOUT_SECONDS = 5
JOB_WAIT_TIMEOUT_SECONDS = 180
JOB_WAIT_POLL_INTERVAL_SECONDS = 2
JOB_WAIT_PROGRESS_INTERVAL_SECONDS = 15


def job_from_cronjob(cronjob: client.V1CronJob, job_name: str, namespace: str) -> client.V1Job:
    """Build a one-off Job using the CronJob template and its metadata."""
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
    conditions = job.status.conditions if job.status else []
    for condition in conditions or []:
        if condition.type == condition_type:
            return condition.status
    return None


def job_pod_diagnostics(
    core_api: client.CoreV1Api,
    namespace: str,
    job_name: str,
) -> str:
    """Collect pod phases, exit codes, and event reasons for a Job."""
    lines = [f"Job {job_name} diagnostics in namespace {namespace}:"]
    try:
        pods = read_kubernetes_response(
            core_api.api_client,
            core_api.list_namespaced_pod,
            "V1PodList",
            namespace=namespace,
            label_selector=f"job-name={job_name}",
            _request_timeout=JOB_DIAGNOSTICS_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return f"{lines[0]} failed to list pods ({type(exc).__name__})"

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
            events = read_kubernetes_response(
                core_api.api_client,
                core_api.list_namespaced_event,
                "CoreV1EventList",
                namespace=namespace,
                field_selector=f"involvedObject.name={pod.metadata.name}",
                _request_timeout=JOB_DIAGNOSTICS_REQUEST_TIMEOUT_SECONDS,
            )
            for event in events.items:
                lines.append(
                    f"  event type={event.type} reason={event.reason}"
                )
        except Exception as exc:
            lines.append(f"  failed to list events ({type(exc).__name__})")
    return "\n".join(lines)


def wait_for_job_complete(
    batch_api: client.BatchV1Api,
    core_api: client.CoreV1Api,
    job_name: str,
    namespace: str,
    *,
    job_description: str,
) -> bool:
    """Wait for completion and include pod diagnostics on failure or timeout."""

    @wait_until(
        description=f"{job_description} Job {job_name} to complete",
        timeout=JOB_WAIT_TIMEOUT_SECONDS,
        interval=JOB_WAIT_POLL_INTERVAL_SECONDS,
        until=lambda complete: complete,
        retry_rules={ApiException: lambda exc: exc.status == 404, HTTPError: None},
        progress_interval=JOB_WAIT_PROGRESS_INTERVAL_SECONDS,
    )
    def wait_for_completion() -> bool:
        try:
            job = read_kubernetes_response(
                batch_api.api_client,
                batch_api.read_namespaced_job_status,
                "V1Job",
                name=job_name,
                namespace=namespace,
                _request_timeout=JOB_API_REQUEST_TIMEOUT_SECONDS,
            )
        except ApiException as exc:
            raise ApiException(status=exc.status) from None
        except HTTPError as exc:
            raise HTTPError(type(exc).__name__) from None

        if _condition_status(job, "Failed") == "True":
            diagnostics = job_pod_diagnostics(core_api, namespace, job_name)
            pytest.fail(
                f"{job_description} Job {job_name} Failed before completion.\n"
                f"{diagnostics}"
            )

        if _condition_status(job, "Complete") != "True":
            return False

        logger.info("%s Job %s completed successfully", job_description, job_name)
        logger.info(
            "Completed %s job diagnostics:\n%s",
            job_description.lower(),
            job_pod_diagnostics(core_api, namespace, job_name),
        )
        return True

    try:
        return wait_for_completion()
    except WaitTimeoutError as exc:
        diagnostics = job_pod_diagnostics(core_api, namespace, job_name)
        pytest.fail(
            f"{job_description} Job {job_name} did not Complete within "
            f"{JOB_WAIT_TIMEOUT_SECONDS}s.\n{diagnostics}\n{exc}"
        )
