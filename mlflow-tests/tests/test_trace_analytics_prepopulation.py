"""Opt-in live Job test for the trace analytics prepopulation handoff."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path

import mlflow
import pytest
import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.entities.trace_status import TraceStatus

from mlflow_tests.utils.client import ClientManager

from .base import TestBase
from .constants.config import Config

logger = logging.getLogger(__name__)
JOB_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "config/samples/trace_analytics_prepopulation_job.yaml"
)
JOB_TIMEOUT_SECONDS = 1800
POLL_SECONDS = 3


def _job_logs(core_api: client.CoreV1Api, namespace: str, name: str) -> str:
    pods = core_api.list_namespaced_pod(namespace, label_selector=f"job-name={name}")
    logs = []
    for pod in pods.items:
        try:
            body = core_api.read_namespaced_pod_log(pod.metadata.name, namespace)
        except ApiException as exc:
            body = f"logs unavailable: {exc.reason}"
        logs.append(f"{pod.metadata.name}:\n{body}")
    return "\n".join(logs) or "No Job pod found"


def _assert_live_and_no_migration(
    apps_api: client.AppsV1Api, batch_api: client.BatchV1Api, namespace: str
) -> None:
    deployment = apps_api.read_namespaced_deployment("mlflow", namespace)
    assert (deployment.status.available_replicas or 0) > 0, (
        "MLflow must remain available during prepopulation"
    )
    migrations = batch_api.list_namespaced_job(
        namespace, label_selector="mlflow.opendatahub.io/migration-job=true"
    )
    for migration in migrations.items:
        conditions = {c.type: c.status for c in migration.status.conditions or []}
        assert not (
            conditions.get("Complete") != "True"
            and conditions.get("Failed") != "True"
        ), f"Operator migration Job {migration.metadata.name} overlaps prepopulation"


def _wait_for_job(
    batch_api: client.BatchV1Api,
    core_api: client.CoreV1Api,
    apps_api: client.AppsV1Api,
    namespace: str,
    name: str,
) -> str:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _assert_live_and_no_migration(apps_api, batch_api, namespace)
        job = batch_api.read_namespaced_job_status(name, namespace)
        conditions = {c.type: c.status for c in job.status.conditions or []}
        if conditions.get("Complete") == "True":
            return _job_logs(core_api, namespace, name)
        if conditions.get("Failed") == "True":
            pytest.fail(f"Prepopulation Job {name} failed:\n{_job_logs(core_api, namespace, name)}")
        time.sleep(POLL_SECONDS)
    pytest.fail(
        f"Prepopulation Job {name} timed out after {JOB_TIMEOUT_SECONDS}s:\n"
        f"{_job_logs(core_api, namespace, name)}"
    )


@pytest.mark.prepopulation
@pytest.mark.skipif(
    not os.getenv("MLFLOW_PREPOPULATION_IMAGE"),
    reason="Set MLFLOW_PREPOPULATION_IMAGE to an image containing the prepopulation command",
)
class TestTraceAnalyticsPrepopulation(TestBase):
    def test_sample_job_runs_twice_while_mlflow_serves(self) -> None:
        """Exercise the shipped Job with a Secret-backed primary on a predecessor schema."""
        ClientManager.load_k8s_config()
        namespace = Config.MLFLOW_NAMESPACE
        apps_api = client.AppsV1Api()
        batch_api = client.BatchV1Api()
        core_api = client.CoreV1Api()
        cr = client.CustomObjectsApi().get_cluster_custom_object(
            "mlflow.opendatahub.io", "v1", "mlflows", "mlflow"
        )
        backend_ref = cr["spec"].get("backendStoreUriFrom")
        assert backend_ref and backend_ref.get("name") and backend_ref.get("key"), (
            "Prepopulation integration requires a Secret-backed primary backend URI"
        )
        deployment = apps_api.read_namespaced_deployment("mlflow", namespace)
        mlflow_container = next(
            container
            for container in deployment.spec.template.spec.containers
            if container.name == "mlflow"
        )
        deployed_ref = next(
            var.value_from.secret_key_ref
            for var in mlflow_container.env or []
            if var.name == "MLFLOW_BACKEND_STORE_URI"
        )
        assert (deployed_ref.name, deployed_ref.key) == (
            backend_ref["name"],
            backend_ref["key"],
        )

        _assert_live_and_no_migration(apps_api, batch_api, namespace)
        original_generation = cr["metadata"]["generation"]
        original_version = cr.get("status", {}).get("version")
        original_image = mlflow_container.image
        self.admin_client.search_experiments()

        workspace = Config.WORKSPACES[0]
        mlflow.set_workspace(workspace)
        experiment_id = self.admin_client.create_experiment(
            f"prepopulation-e2e-{uuid.uuid4().hex}"
        )
        self.test_context.add_experiment_for_cleanup(experiment_id, workspace)
        span = self.admin_client.start_trace(
            name=f"prepopulation-e2e-{uuid.uuid4().hex}",
            experiment_id=experiment_id,
        )
        trace_id = getattr(span, "request_id", None) or getattr(span, "trace_id", None)
        assert trace_id and trace_id != NO_OP_SPAN_TRACE_ID
        self.admin_client.end_trace(request_id=trace_id, status=TraceStatus.OK)

        created_jobs = []
        try:
            for run in range(2):
                job = yaml.safe_load(JOB_TEMPLATE.read_text())
                name = f"mlflow-trace-prepopulate-e2e-{uuid.uuid4().hex[:8]}"
                job["metadata"]["name"] = name
                job["metadata"]["namespace"] = namespace
                container = job["spec"]["template"]["spec"]["containers"][0]
                container["image"] = os.environ["MLFLOW_PREPOPULATION_IMAGE"]
                container["env"][0]["valueFrom"]["secretKeyRef"] = backend_ref
                pod_spec = job["spec"]["template"]["spec"]
                if deployment.spec.template.spec.image_pull_secrets:
                    pod_spec["imagePullSecrets"] = [
                        client.ApiClient().sanitize_for_serialization(secret)
                        for secret in deployment.spec.template.spec.image_pull_secrets
                    ]
                # Reuse the live Deployment's database trust setup when it builds a CA bundle.
                for var in mlflow_container.env or []:
                    if var.name.startswith("PG") or var.name in {
                        "SSL_CERT_FILE",
                        "REQUESTS_CA_BUNDLE",
                        "MLFLOW_MYSQL_CA",
                    }:
                        container["env"].append(client.ApiClient().sanitize_for_serialization(var))
                ca_init = [
                    item
                    for item in deployment.spec.template.spec.init_containers or []
                    if item.name == "combine-ca-bundles"
                ]
                if ca_init:
                    pod_spec["initContainers"] = [
                        client.ApiClient().sanitize_for_serialization(item)
                        for item in ca_init
                    ]
                    ca_mounts = [
                        mount
                        for mount in mlflow_container.volume_mounts or []
                        if mount.name == "combined-ca-bundle"
                    ]
                    container["volumeMounts"] = [
                        client.ApiClient().sanitize_for_serialization(mount)
                        for mount in ca_mounts
                    ]
                    volume_names = {
                        mount.name
                        for item in ca_init
                        for mount in item.volume_mounts or []
                    } | {mount.name for mount in ca_mounts}
                    pod_spec["volumes"] = [
                        client.ApiClient().sanitize_for_serialization(volume)
                        for volume in deployment.spec.template.spec.volumes or []
                        if volume.name in volume_names
                    ]
                batch_api.create_namespaced_job(namespace, job)
                created_jobs.append(name)
                logs = _wait_for_job(batch_api, core_api, apps_api, namespace, name)
                logger.info("Prepopulation run %d:\n%s", run + 1, logs)
                assert "Prepopulation completed without advancing the Alembic revision" in logs
                for entity in ("Traces", "Spans", "Assessments"):
                    assert f"{entity}: scanned=" in logs, logs
                if run == 0:
                    scanned = re.search(r"Traces: scanned=(\d+)", logs)
                    assert scanned and int(scanned.group(1)) > 0, logs
                else:
                    # The second pass must finish cleanly over the rows from the first.
                    assert re.search(r"Traces: scanned=\d+, updated=0", logs), logs
                    assert re.search(r"Spans: scanned=\d+, updated=0", logs), logs
                    assert re.search(r"Assessments: scanned=\d+, updated=0", logs), logs
                self.admin_client.search_experiments()
                assert self.admin_client.get_trace(trace_id).info.trace_id == trace_id

            current = client.CustomObjectsApi().get_cluster_custom_object(
                "mlflow.opendatahub.io", "v1", "mlflows", "mlflow"
            )
            assert current["metadata"]["generation"] == original_generation
            assert current.get("status", {}).get("version") == original_version
            assert (
                next(
                    container.image
                    for container in apps_api.read_namespaced_deployment(
                        "mlflow", namespace
                    ).spec.template.spec.containers
                    if container.name == "mlflow"
                )
                == original_image
            )
        finally:
            for name in created_jobs:
                try:
                    batch_api.delete_namespaced_job(
                        name, namespace, propagation_policy="Background"
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        raise
