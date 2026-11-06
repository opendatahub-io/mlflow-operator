"""Shared diagnostics and polling helpers for Jobs based on CronJob templates."""

from __future__ import annotations

import logging

import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from mlflow_tests.utils.wait import WaitTimeoutError, wait_until

logger = logging.getLogger(__name__)
JOB_WAIT_TIMEOUT_SECONDS = 180
JOB_WAIT_POLL_INTERVAL_SECONDS = 2


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
    *,
    container_name: str,
    tail_lines: int | None = 200,
) -> str:
    """Return pod status, events, and logs for a Job."""
    lines = [f"Job {job_name} diagnostics in namespace {namespace}:"]
    try:
        pods = core_api.list_namespaced_pod(
            namespace, label_selector=f"job-name={job_name}"
        )
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
            log_options: dict[str, str | int] = {"container": container_name}
            if tail_lines is not None:
                log_options["tail_lines"] = tail_lines
            logs = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                **log_options,
            )
            lines.append(f"  logs:\n{logs}")
        except ApiException as exc:
            lines.append(f"  logs unavailable: {exc.reason}")
    return "\n".join(lines)


def wait_for_job_complete(
    batch_api: client.BatchV1Api,
    core_api: client.CoreV1Api,
    job_name: str,
    namespace: str,
    *,
    container_name: str,
    job_description: str,
) -> bool:
    """Wait for completion and include pod diagnostics on failure or timeout."""

    @wait_until(
        description=f"{job_description} Job to complete",
        timeout=JOB_WAIT_TIMEOUT_SECONDS,
        interval=JOB_WAIT_POLL_INTERVAL_SECONDS,
        until=lambda complete: complete,
        retry_rules={ApiException: lambda exc: exc.status == 404},
    )
    def wait_for_completion() -> bool:
        job = batch_api.read_namespaced_job_status(name=job_name, namespace=namespace)

        if _condition_status(job, "Failed") == "True":
            diagnostics = job_pod_diagnostics(
                core_api,
                namespace,
                job_name,
                container_name=container_name,
            )
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
            job_pod_diagnostics(
                core_api,
                namespace,
                job_name,
                container_name=container_name,
            ),
        )
        return True

    try:
        return wait_for_completion()
    except WaitTimeoutError as exc:
        diagnostics = job_pod_diagnostics(
            core_api,
            namespace,
            job_name,
            container_name=container_name,
        )
        pytest.fail(
            f"{job_description} Job {job_name} did not Complete within "
            f"{JOB_WAIT_TIMEOUT_SECONDS}s.\n{diagnostics}\n{exc}"
        )
