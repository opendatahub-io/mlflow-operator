"""Live garbage-collection smoke coverage against remote SQL and object storage."""

from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import boto3
import mlflow
import pytest
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from mlflow_tests.utils.client import ClientManager

from .base import TestBase
from .constants.config import Config

logger = logging.getLogger(__name__)

GC_CRONJOB_NAME = "mlflow-gc"
GC_CONTAINER_NAME = "mlflow-gc"
POLL_INTERVAL_SECONDS = 2
JOB_TIMEOUT_SECONDS = 300
OBJECT_TIMEOUT_SECONDS = 30


def _s3_client():
    kwargs = {
        "service_name": "s3",
        "aws_access_key_id": Config.AWS_ACCESS_KEY,
        "aws_secret_access_key": Config.AWS_SECRET_KEY,
    }
    if Config.S3_URL:
        kwargs["endpoint_url"] = Config.S3_URL
        kwargs["config"] = BotocoreConfig(s3={"addressing_style": "path"})
        if (
            Config.S3_URL.startswith("https://localhost:")
            and Config.DISABLE_TLS == "true"
        ):
            kwargs["verify"] = False
    return boto3.client(**kwargs)


def _job_from_cronjob(
    cronjob: client.V1CronJob, name: str, insecure_tls: str
) -> client.V1Job:
    template = cronjob.spec.job_template
    metadata = client.V1ObjectMeta(
        name=name,
        namespace=cronjob.metadata.namespace,
        labels=dict(template.metadata.labels or {}),
        annotations={"cronjob.kubernetes.io/instantiate": "manual"},
    )
    job = client.V1Job(metadata=metadata, spec=template.spec)
    container = next(
        container
        for container in job.spec.template.spec.containers
        if container.name == GC_CONTAINER_NAME
    )
    for env_var in container.env or []:
        if env_var.name == "MLFLOW_TRACKING_INSECURE_TLS":
            env_var.value = insecure_tls
            break
    return job


def _artifact_object_key(artifact_uri: str, artifact_path: str, filename: str) -> str:
    parsed = urlparse(artifact_uri)
    if parsed.scheme == "s3":
        if parsed.netloc != Config.S3_BUCKET:
            pytest.fail(
                f"GC smoke artifact bucket {parsed.netloc!r} is not {Config.S3_BUCKET!r}"
            )
        prefix = parsed.path.lstrip("/")
    elif parsed.scheme == "mlflow-artifacts":
        # Proxy locations are relative to the configured s3://<bucket>/artifacts root.
        prefix = f"artifacts/{parsed.path.lstrip('/')}"
    elif parsed.scheme in {"http", "https"}:
        marker = "/api/2.0/mlflow-artifacts/"
        _, separator, prefix = parsed.path.partition(marker)
        if not separator:
            pytest.fail(
                "GC smoke expected an MLflow artifacts-server URI, got "
                f"{artifact_uri!r}"
            )
    else:
        pytest.fail(
            "GC smoke expected an S3, mlflow-artifacts, or artifacts-server URI, got "
            f"{artifact_uri!r}"
        )
    return f"{prefix}/{artifact_path}/{filename}"


def _request_job_stack_dumps(
    core_api: client.CoreV1Api, name: str, namespace: str
) -> None:
    pods = core_api.list_namespaced_pod(namespace, label_selector=f"job-name={name}")
    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        try:
            stream(
                core_api.connect_get_namespaced_pod_exec,
                pod.metadata.name,
                namespace,
                command=["/bin/sh", "-c", "kill -USR1 1"],
                container=GC_CONTAINER_NAME,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
        except ApiException as exc:
            logger.warning(
                "Unable to request a GC stack dump from %s: %s", pod.metadata.name, exc
            )


def _job_pod_diagnostics(
    core_api: client.CoreV1Api,
    name: str,
    namespace: str,
    request_stack_dump: bool = False,
) -> str:
    if request_stack_dump:
        _request_job_stack_dumps(core_api, name, namespace)
        time.sleep(POLL_INTERVAL_SECONDS)
    pods = core_api.list_namespaced_pod(namespace, label_selector=f"job-name={name}")
    diagnostics = []
    for pod in pods.items:
        try:
            logs = core_api.read_namespaced_pod_log(
                pod.metadata.name, namespace, container=GC_CONTAINER_NAME
            )
        except ApiException as exc:
            logs = f"logs unavailable: {exc.reason}"
        diagnostics.append(f"{pod.metadata.name} ({pod.status.phase}):\n{logs}")
    return "\n".join(diagnostics) or "No pods found for the Job"


def _wait_for_job(
    batch_api: client.BatchV1Api, core_api: client.CoreV1Api, name: str, namespace: str
) -> None:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        job = batch_api.read_namespaced_job_status(name, namespace)
        conditions = {
            condition.type: condition.status
            for condition in job.status.conditions or []
        }
        if conditions.get("Complete") == "True":
            return
        if conditions.get("Failed") == "True":
            pytest.fail(
                f"Garbage collection Job {name} failed:\n"
                + _job_pod_diagnostics(core_api, name, namespace)
            )
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"Garbage collection Job {name} did not complete within {JOB_TIMEOUT_SECONDS}s:\n"
        + _job_pod_diagnostics(core_api, name, namespace, request_stack_dump=True)
    )


@pytest.mark.smoke
@pytest.mark.skipif(
    Config.ARTIFACT_STORAGE != "s3" or not Config.GARBAGE_COLLECTION_ENABLED,
    reason="garbage collection live Job requires enabled remote SQL/S3 deployment",
)
class TestGarbageCollection(TestBase):
    def test_garbage_collection_job_removes_deleted_run_and_artifacts(self) -> None:
        """Instantiate the operator CronJob and prove it removes a deleted run and artifact."""
        assert Config.S3_BUCKET, "GC smoke requires AWS_S3_BUCKET"
        assert Config.AWS_ACCESS_KEY and Config.AWS_SECRET_KEY, (
            "GC smoke requires S3 credentials"
        )
        workspace = Config.WORKSPACES[0]
        self.test_context.active_workspace = workspace
        mlflow.set_workspace(workspace)

        experiment_id = self.admin_client.create_experiment(
            f"gc-smoke-{uuid.uuid4().hex}",
        )
        self.test_context.add_experiment_for_cleanup(experiment_id, workspace)
        run = self.admin_client.create_run(experiment_id)
        run_id = run.info.run_id
        self.test_context.add_run_for_cleanup(run_id, workspace)
        artifact_path = "garbage-collection-smoke"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as artifact:
            artifact.write("garbage collection smoke artifact")
            artifact.flush()
            artifact_filename = Path(artifact.name).name
            self.admin_client.log_artifact(run_id, artifact.name, artifact_path)
        self.admin_client.set_terminated(run_id)

        artifact_uri = self.admin_client.get_run(run_id).info.artifact_uri
        object_key = _artifact_object_key(
            artifact_uri, artifact_path, artifact_filename
        )

        s3 = _s3_client()
        deadline = time.monotonic() + OBJECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                s3.head_object(Bucket=Config.S3_BUCKET, Key=object_key)
                break
            except ClientError:
                time.sleep(POLL_INTERVAL_SECONDS)
        else:
            pytest.fail(f"Artifact {object_key} was not written to S3 before GC")

        self.admin_client.delete_experiment(experiment_id)

        ClientManager.load_k8s_config()
        batch_api = client.BatchV1Api()
        core_api = client.CoreV1Api()
        cronjob = batch_api.read_namespaced_cron_job(
            GC_CRONJOB_NAME, Config.MLFLOW_NAMESPACE
        )
        job_name = f"gc-e2e-{uuid.uuid4().hex[:8]}"
        batch_api.create_namespaced_job(
            Config.MLFLOW_NAMESPACE,
            _job_from_cronjob(cronjob, job_name, Config.DISABLE_TLS),
        )
        self.test_context.add_job_for_cleanup(job_name, Config.MLFLOW_NAMESPACE)
        _wait_for_job(batch_api, core_api, job_name, Config.MLFLOW_NAMESPACE)

        with pytest.raises(Exception):
            self.admin_client.get_run(run_id)
        with pytest.raises(Exception):
            self.admin_client.get_experiment(experiment_id)
        deadline = time.monotonic() + OBJECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                s3.head_object(Bucket=Config.S3_BUCKET, Key=object_key)
            except ClientError as exc:
                if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                    return
                raise
            time.sleep(POLL_INTERVAL_SECONDS)
        pytest.fail(
            f"GC removed run {run_id} but left artifact s3://{Config.S3_BUCKET}/{object_key}"
        )
