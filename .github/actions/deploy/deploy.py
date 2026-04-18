#!/usr/bin/env python3
"""
MLflow Deployment Script for Kubernetes Clusters

This script deploys MLflow operator and creates an MLflow instance with configurable
storage backends (SQLite/PostgreSQL) and artifact storage (file/S3).
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Union
from urllib.parse import quote_plus

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
import yaml

DEFAULT_COMMAND_TIMEOUT = 300


class MLflowDeployer:
    def __init__(self, args):
        self.args = args
        self.repo_root = Path(__file__).parent.parent.parent.parent
        self._tls_ca_bundle_cm = None
        self._ca_tmpdir = None
        self._ca_cert_pem = None

        # Set default endpoints if not provided
        # For externals3 the endpoint is caller-supplied; don't override with the
        # in-cluster SeaweedFS/minio address.
        if not self.args.s3_endpoint and self.args.artifact_storage != "externals3":
            scheme = "https" if self.args.seaweedfs_tls else "http"
            self.args.s3_endpoint = f"{scheme}://minio-service.{self.args.namespace}.svc.cluster.local:9000"

        if not self.args.postgres_host:
            self.args.postgres_host = f"postgres-service.{self.args.namespace}.svc.cluster.local"

        # Apply sslmode default: 'verify-full' when TLS is enabled (validates the
        # server cert against the CA bundle), 'disable' otherwise.
        # None means the caller didn't pass --postgres-sslmode explicitly.
        if self.args.postgres_sslmode is None:
            self.args.postgres_sslmode = "verify-full" if self.args.postgres_tls else "disable"
            print(f"  PostgreSQL sslmode defaulted to '{self.args.postgres_sslmode}'")

        self.api_client = self._load_kubernetes_client()
        self.core_api = client.CoreV1Api(self.api_client)
        self.apps_api = client.AppsV1Api(self.api_client)
        self.batch_api = client.BatchV1Api(self.api_client)
        self.custom_api = client.CustomObjectsApi(self.api_client)

        print(f"Repository root: {self.repo_root}")
        print(f"Target namespace: {self.args.namespace}")
        print(f"S3 endpoint: {self.args.s3_endpoint}")
        print(f"PostgreSQL host: {self.args.postgres_host}")

    def run_command(self,
                    cmd: Union[str, List[str]],
                    description=None,
                    check=True,
                    capture_output=False,
                    timeout: int = 300) -> subprocess.CompletedProcess:
        """Run shell command with streaming output to avoid memory issues."""
        if description:
            print(f"📋 {description}")

        # Handle both string and list commands
        if isinstance(cmd, str):
            cmd_str = cmd
            cmd_args = cmd
            shell = True
        else:
            cmd_str = ' '.join(cmd)
            cmd_args = cmd
            shell = False

        # Only print command info if not capturing output silently
        if not capture_output:
            print(f"🔧 Running: {cmd_str}")
            if timeout:
                print(f'⏱️  Timeout: {timeout} seconds')

        process = None
        try:
            # Use Popen for streaming output instead of run() to avoid memory issues
            process = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True,
                shell=shell)

            # Use communicate with timeout to enforce timeout properly
            stdout, stderr = process.communicate(timeout=timeout)
            return_code = process.returncode

            # Split into lines for compatibility
            output_lines = stdout.rstrip('\n').split('\n') if stdout.rstrip('\n') else []

            # Print output if not capturing silently
            if not capture_output and output_lines:
                for line in output_lines:
                    print(line, flush=True)

            # Create a mock CompletedProcess for compatibility
            result = subprocess.CompletedProcess(
                args=cmd_args,
                returncode=return_code,
                stdout='\n'.join(output_lines),
                stderr=''  # Already merged into stdout
            )

            if check and return_code != 0:
                raise subprocess.CalledProcessError(
                    return_code, cmd_args, output=result.stdout)

            return result

        except subprocess.TimeoutExpired as e:
            if not capture_output:
                print(f'⏰ Command timed out after {timeout} seconds')
                print(f'❌ Timeout command: {cmd_str}')
            raise
        except subprocess.CalledProcessError as e:
            # Error details already streamed during execution
            if not capture_output:
                print(f'❌ Command failed with exit code {e.returncode}')
            raise
        except Exception as e:
            if not capture_output:
                print(f'❌ Unexpected error running command: {e}')
            if process is not None:
                if process.poll() is None:  # Check if process is still running
                    process.kill()
                process.wait()
            raise

    def _load_kubernetes_client(self) -> client.ApiClient:
        """Load Kubernetes credentials from the pod service account or kubeconfig."""
        try:
            config.load_incluster_config()
            auth_source = "in-cluster service account"
        except ConfigException:
            config.load_kube_config()
            auth_source = "kubeconfig"

        print(f"🔐 Loaded Kubernetes credentials from {auth_source}")
        return client.ApiClient()

    @staticmethod
    def _is_not_found(exc: ApiException) -> bool:
        return exc.status == 404

    def _serialize_k8s_object(self, obj):
        if obj is None:
            return None
        return self.api_client.sanitize_for_serialization(obj)

    def _delete_if_exists(self, delete_fn, *args, **kwargs):
        try:
            delete_fn(*args, **kwargs)
        except ApiException as exc:
            if not self._is_not_found(exc):
                raise

    def _read_optional(self, read_fn, *args, **kwargs):
        try:
            return read_fn(*args, **kwargs)
        except ApiException as exc:
            if self._is_not_found(exc):
                return None
            raise

    def _apply_secret(self, name: str, string_data=None, file_data=None):
        namespace = self.args.namespace
        combined_string_data = dict(string_data or {})
        for key, path in (file_data or {}).items():
            with open(path) as f:
                combined_string_data[key] = f.read()

        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            string_data=combined_string_data,
            type="Opaque",
        )
        existing = self._read_optional(self.core_api.read_namespaced_secret, name, namespace)
        if existing:
            body.metadata.resource_version = existing.metadata.resource_version
            self.core_api.patch_namespaced_secret(name, namespace, body)
        else:
            self.core_api.create_namespaced_secret(namespace, body)

    def _apply_configmap(self, name: str, data: dict):
        namespace = self.args.namespace
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            data=data,
        )
        existing = self._read_optional(self.core_api.read_namespaced_config_map, name, namespace)
        if existing:
            body.metadata.resource_version = existing.metadata.resource_version
            self.core_api.patch_namespaced_config_map(name, namespace, body)
        else:
            self.core_api.create_namespaced_config_map(namespace, body)

    def _apply_mlflow_cr(self, mlflow_cr: dict):
        name = mlflow_cr["metadata"]["name"]
        existing = None
        try:
            existing = self.custom_api.get_cluster_custom_object(
                group="mlflow.opendatahub.io",
                version="v1",
                plural="mlflows",
                name=name,
            )
        except ApiException as exc:
            if not self._is_not_found(exc):
                raise

        if existing:
            patch_body = copy.deepcopy(mlflow_cr)
            patch_metadata = {"name": name}
            existing_metadata = existing.get("metadata", {})
            patch_metadata["resourceVersion"] = existing_metadata["resourceVersion"]
            for field in ("finalizers", "labels", "annotations"):
                value = existing_metadata.get(field)
                if value:
                    patch_metadata[field] = value
            patch_body["metadata"] = patch_metadata
            self.custom_api.patch_cluster_custom_object(
                group="mlflow.opendatahub.io",
                version="v1",
                plural="mlflows",
                name=name,
                body=patch_body,
            )
        else:
            self.custom_api.create_cluster_custom_object(
                group="mlflow.opendatahub.io",
                version="v1",
                plural="mlflows",
                body=mlflow_cr,
            )

    def _build_kustomize(self, path: Path, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> str:
        try:
            return subprocess.run(
                ["kustomize", "build", str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            ).stdout
        except subprocess.CalledProcessError as exc:
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise
        except subprocess.TimeoutExpired as exc:
            print(f"❌ kustomize build timed out after {timeout} seconds", file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise

    def _run_envsubst(self, manifests: str, extra_env=None,
                      timeout: int = DEFAULT_COMMAND_TIMEOUT) -> str:
        env = {**os.environ, **(extra_env or {})}
        try:
            return subprocess.run(
                ["envsubst"],
                input=manifests,
                capture_output=True,
                text=True,
                check=True,
                env=env,
                timeout=timeout,
            ).stdout
        except subprocess.CalledProcessError as exc:
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise
        except subprocess.TimeoutExpired as exc:
            print(f"❌ envsubst timed out after {timeout} seconds", file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise

    def _apply_manifest_stream(self, manifests: str, namespace: str = None,
                               timeout: int = DEFAULT_COMMAND_TIMEOUT):
        cmd = ["kubectl", "apply"]
        if namespace:
            cmd += ["-n", namespace]
        cmd += ["-f", "-"]
        try:
            subprocess.run(
                cmd,
                input=manifests,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise
        except subprocess.TimeoutExpired as exc:
            print(f"❌ kubectl apply timed out after {timeout} seconds", file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
            if exc.stdout:
                print(exc.stdout)
            raise

    def _wait_for_pods_to_be_deleted(self, label_selector: str, namespace: str,
                                     timeout: int = 60, poll_interval: int = 5):
        elapsed_time = 0
        while elapsed_time < timeout:
            pods = self.core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            ).items
            if not pods:
                return True
            time.sleep(poll_interval)
            elapsed_time += poll_interval
        print(
            f"⚠️  Timed out waiting for pods matching '{label_selector}' "
            f"to be deleted in namespace '{namespace}'"
        )
        return False

    def _wait_for_resource_absent(self, read_fn, description: str, *args,
                                  timeout: int = 60, poll_interval: int = 5):
        elapsed_time = 0
        while elapsed_time < timeout:
            if self._read_optional(read_fn, *args) is None:
                return True
            time.sleep(poll_interval)
            elapsed_time += poll_interval
        raise Exception(f"Timed out waiting for {description} to be deleted after {timeout}s")

    def _wait_for_deployment_available(self, deployment_name: str, namespace: str,
                                       timeout: int = 300, poll_interval: int = 10):
        print(f"⏳ Waiting for {deployment_name} deployment to become available...")
        elapsed_time = 0
        while elapsed_time < timeout:
            deployment = self._read_optional(
                self.apps_api.read_namespaced_deployment,
                deployment_name,
                namespace,
            )
            if deployment and deployment.status:
                desired_replicas = (
                    deployment.spec.replicas
                    if deployment.spec and deployment.spec.replicas is not None
                    else 1
                )
                observed_generation = deployment.status.observed_generation or 0
                current_generation = deployment.metadata.generation or 0
                updated_replicas = deployment.status.updated_replicas or 0
                ready_replicas = deployment.status.ready_replicas or 0
                conditions = deployment.status.conditions or []
                is_available = any(c.type == "Available" and c.status == "True" for c in conditions)
                if (
                    is_available
                    and observed_generation >= current_generation
                    and updated_replicas >= desired_replicas
                    and ready_replicas >= desired_replicas
                ):
                    print(f"✅ {deployment_name} deployment is available")
                    return True
            time.sleep(poll_interval)
            elapsed_time += poll_interval
            if elapsed_time < timeout:
                print(
                    f"⏳ Waiting for {deployment_name} deployment to become available... "
                    f"({elapsed_time}/{timeout}s)"
                )
        raise Exception(f"Timeout waiting for {deployment_name} deployment to become available after {timeout}s")

    def _wait_for_job_completion(self, job_name: str, namespace: str,
                                 timeout: int = 400, poll_interval: int = 10):
        print(f"⏳ Waiting for {job_name} job to complete...")
        elapsed_time = 0
        while elapsed_time < timeout:
            job = self._read_optional(self.batch_api.read_namespaced_job, job_name, namespace)
            if job and job.status:
                conditions = job.status.conditions or []
                if any(c.type == "Complete" and c.status == "True" for c in conditions):
                    print(f"✅ {job_name} job completed successfully")
                    return True
                if any(c.type == "Failed" and c.status == "True" for c in conditions):
                    raise Exception(f"Job {job_name} failed")
            time.sleep(poll_interval)
            elapsed_time += poll_interval
            if elapsed_time < timeout:
                print(f"⏳ Waiting for {job_name} job to complete... ({elapsed_time}/{timeout}s)")
        raise Exception(f"Timeout waiting for {job_name} job to complete after {timeout}s")

    @staticmethod
    def _selector_to_string(match_labels: dict) -> str:
        return ",".join(f"{key}={value}" for key, value in match_labels.items())

    def _deployment_label_selector(self, deployment_name: str, namespace: str) -> str:
        deployment = self._read_optional(
            self.apps_api.read_namespaced_deployment,
            deployment_name,
            namespace,
        )
        if deployment and deployment.spec and deployment.spec.selector and deployment.spec.selector.match_labels:
            return self._selector_to_string(deployment.spec.selector.match_labels)
        return f"app={deployment_name}"

    def _read_pod_phase(self, pod_name: str, namespace: str) -> str:
        pod = self._read_optional(self.core_api.read_namespaced_pod, pod_name, namespace)
        if pod and pod.status and pod.status.phase:
            return pod.status.phase
        return ""

    def _get_mlflow_cr(self):
        try:
            return self.custom_api.get_cluster_custom_object(
                group="mlflow.opendatahub.io",
                version="v1",
                plural="mlflows",
                name="mlflow",
            )
        except ApiException as exc:
            if self._is_not_found(exc):
                return None
            raise

    def create_namespace(self):
        """Create Kubernetes namespace"""
        print(f"🔨 Creating namespace '{self.args.namespace}'...")
        namespace = self._read_optional(self.core_api.read_namespace, self.args.namespace)
        if namespace:
            print(f"✅ Namespace '{self.args.namespace}' already exists")
        else:
            self.core_api.create_namespace(
                client.V1Namespace(metadata=client.V1ObjectMeta(name=self.args.namespace))
            )
            print(f"✅ Namespace '{self.args.namespace}' created")

    def generate_tls_certificates(self):
        """Generate TLS certificates for MLflow operator deployment"""
        print("🔐 Generating TLS certificates...")

        kind_overlay = self.repo_root / "config" / "overlays" / "kind"
        generate_tls_script = kind_overlay / "generate-tls.sh"

        # Generate certificate and key files
        env = {**os.environ, "NAMESPACE": self.args.namespace}
        subprocess.run(
            [str(generate_tls_script), "generate"],
            check=True, env=env,
        )

        # Verify the files were created
        cert_file = kind_overlay / "tls.crt"
        key_file = kind_overlay / "tls.key"

        if not cert_file.exists():
            raise Exception(f"TLS certificate file was not created: {cert_file}")
        if not key_file.exists():
            raise Exception(f"TLS private key file was not created: {key_file}")

        print(f"✅ TLS certificate generated: {cert_file}")
        print(f"✅ TLS private key generated: {key_file}")

    def cleanup_tls_certificates(self):
        """Clean up TLS resources created for this deployment."""
        print("🧹 Cleaning up TLS resources...")

        if self.args.seaweedfs_tls:
            self._delete_if_exists(
                self.core_api.delete_namespaced_secret,
                "seaweedfs-tls-certs",
                self.args.namespace,
            )

        if self.args.postgres_tls:
            self._delete_if_exists(
                self.core_api.delete_namespaced_secret,
                "postgres-tls-certs",
                self.args.namespace,
            )

        # Delete the CA bundle ConfigMap we created (harmless if absent).
        self._delete_if_exists(
            self.core_api.delete_namespaced_config_map,
            "mlflow-ca-bundle",
            self.args.namespace,
        )

    def deploy_mlflow_operator(self):
        """Deploy MLflow operator using kustomize"""
        print("🚀 Deploying MLflow operator...")

        # Update base params.env to set the correct namespace for the operator
        base_params_env = self.repo_root / "config" / "overlays" / "kind" / "params.env"
        print(f"📝 Updating operator namespace to '{self.args.namespace}' in {base_params_env}")

        self.run_command([
            "sed", "-i", f"s#NAMESPACE=.*#NAMESPACE={self.args.namespace}#", str(base_params_env)
        ], f"Setting operator namespace to {self.args.namespace}")

        self.run_command([
            "sed", "-i", f"s#MLFLOW_OPERATOR_IMAGE=.*#MLFLOW_OPERATOR_IMAGE={self.args.mlflow_operator_image}#", str(base_params_env)
        ], f"Setting operator image to {self.args.mlflow_operator_image}")

        # Generate TLS certificates before building with kustomize
        self.generate_tls_certificates()

        # Use the kind overlay with proper environment setup
        kind_overlay = self.repo_root / "config" / "overlays" / "kind"
        manifests = self._build_kustomize(kind_overlay)
        manifests = self._run_envsubst(manifests, {"NAMESPACE": self.args.namespace})
        self._apply_manifest_stream(manifests)

        # Wait for operator to be ready (now in the correct namespace)
        try:
            self._wait_for_deployment_available(
                "mlflow-operator-controller-manager",
                self.args.namespace,
            )
        except Exception as e:
            print(f"❌ MLflow operator deployment failed to become ready: {e}")
            self.debug_deployment("mlflow-operator-controller-manager", self.args.namespace)
            raise

    def create_postgres_secret(self):
        """Create PostgreSQL credentials secret"""
        print("🔐 Creating PostgreSQL credentials secret...")

        # URL-encode credentials to handle special characters like @, /, :, ;
        encoded_user = quote_plus(self.args.postgres_user)
        encoded_pass = quote_plus(self.args.postgres_password)

        sslmode_param = f"?sslmode={self.args.postgres_sslmode}" if self.args.postgres_sslmode else ""
        backend_uri = f"postgresql://{encoded_user}:{encoded_pass}@{self.args.postgres_host}:{self.args.postgres_port}/{self.args.postgres_backend_db}{sslmode_param}"
        registry_uri = f"postgresql://{encoded_user}:{encoded_pass}@{self.args.postgres_host}:{self.args.postgres_port}/{self.args.postgres_registry_db}{sslmode_param}"
        self._apply_secret("mlflow-db-credentials", {
            "backend-store-uri": backend_uri,
            "registry-store-uri": registry_uri,
        })
        print("✅ PostgreSQL credentials secret ready")

    def create_s3_secret(self):
        """Create S3/AWS credentials secret"""
        print("🔐 Creating S3 credentials secret...")
        secret_data = {
            "AWS_ACCESS_KEY_ID": self.args.s3_access_key,
            "AWS_SECRET_ACCESS_KEY": self.args.s3_secret_key,
        }
        if self.args.s3_region:
            secret_data["AWS_DEFAULT_REGION"] = self.args.s3_region
        self._apply_secret("aws-credentials", secret_data)
        print("✅ S3 credentials secret ready")

    def _ensure_ca(self):
        """Generate a self-signed CA key+cert (once) for signing server certificates.

        The CA cert PEM is stored in self._ca_cert_pem and used as the single
        trust anchor in the CA bundle ConfigMap.
        """
        if self._ca_tmpdir:
            return
        self._ca_tmpdir = tempfile.TemporaryDirectory(prefix="mlflow-ca-")
        ca_dir = self._ca_tmpdir.name
        ca_key = f"{ca_dir}/ca.key"
        ca_cert = f"{ca_dir}/ca.crt"
        self.run_command([
            "openssl", "req", "-new", "-x509", "-days", "3650", "-nodes",
            "-subj", "/CN=mlflow-test-ca",
            "-keyout", ca_key,
            "-out", ca_cert,
        ], "Generating CA certificate", capture_output=True)
        with open(ca_cert) as f:
            self._ca_cert_pem = f.read()
        print("🔐 CA certificate generated")

    def _create_tls_secret(self, secret_name: str, cn: str,
                           cert_filename: str = "tls.crt",
                           key_filename: str = "tls.key",
                           extra_sans: list = None):
        """Generate a server cert signed by the internal CA and create a K8s Secret.

        Calls _ensure_ca() to create the CA if it doesn't exist yet.
        SANs always include DNS:cn plus any extra_sans.
        """
        self._ensure_ca()
        sans = [f"DNS:{cn}"] + (extra_sans or [])
        san_str = ",".join(sans)
        print(f"🔐 Generating CA-signed cert (CN={cn}, SANs={san_str}) for {secret_name}...")
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = f"{tmpdir}/{key_filename}"
            csr_path = f"{tmpdir}/server.csr"
            cert_path = f"{tmpdir}/{cert_filename}"
            ext_path = f"{tmpdir}/ext.cnf"

            with open(ext_path, "w") as f:
                f.write(f"subjectAltName={san_str}\n")

            self.run_command([
                "openssl", "req", "-new", "-nodes",
                "-subj", f"/CN={cn}",
                "-keyout", key_path,
                "-out", csr_path,
            ], capture_output=True)

            self.run_command([
                "openssl", "x509", "-req", "-days", "3650",
                "-CA", f"{self._ca_tmpdir.name}/ca.crt",
                "-CAkey", f"{self._ca_tmpdir.name}/ca.key",
                "-CAcreateserial",
                "-extfile", ext_path,
                "-in", csr_path,
                "-out", cert_path,
            ], f"Signing cert for {cn}", capture_output=True)
            self._apply_secret(
                secret_name,
                file_data={
                    cert_filename: cert_path,
                    key_filename: key_path,
                },
            )
            print(f"✅ TLS Secret {secret_name} ready")

    def _create_infra_ca_bundle(self, combined_pem: str):
        """Create an mlflow-ca-bundle ConfigMap directly in the target namespace."""
        cm_name = "mlflow-ca-bundle"
        self._apply_configmap(cm_name, {"ca-bundle.crt": combined_pem})
        print(f"✅ {cm_name} ConfigMap ready")
        self._tls_ca_bundle_cm = cm_name

    def deploy_seaweedfs(self):
        """Deploy SeaweedFS for S3-compatible storage"""
        print("🌊 Deploying SeaweedFS...")

        if self.args.seaweedfs_tls:
            self._create_tls_secret(
                "seaweedfs-tls-certs", cn="minio-service",
                cert_filename="tls.crt", key_filename="tls.key",
                extra_sans=[
                    f"DNS:minio-service.{self.args.namespace}",
                    f"DNS:minio-service.{self.args.namespace}.svc",
                    f"DNS:minio-service.{self.args.namespace}.svc.cluster.local",
                ],
            )
            platform_overlay = "openshift-tls" if self.args.platform == "openshift" else "tls"
        else:
            platform_overlay = "openshift" if self.args.platform == "openshift" else "base"

        seaweedfs_path = self.repo_root / "config" / "seaweedfs" / platform_overlay
        seaweedfs_params_env = self.repo_root / "config" / "seaweedfs" / "base" / "params.env"

        self.run_command([
            "sed", "-i", f"s#SEAWEEDFS_IMAGE=.*#SEAWEEDFS_IMAGE={self.args.seaweedfs_image}#",
            str(seaweedfs_params_env),
        ], f"Setting seaweedfs image to {self.args.seaweedfs_image}")


        # Note: base params.env namespace already updated by deploy_mlflow_operator()
        print(f"📝 SeaweedFS will deploy to namespace '{self.args.namespace}' (from base params.env)")

        # Idempotency: delete Deployment before PVC so the pod releases the
        # pvc-protection finalizer, then start fresh with a clean filer database.
        self._delete_if_exists(
            self.apps_api.delete_namespaced_deployment,
            "seaweedfs",
            self.args.namespace,
        )
        if not self._wait_for_pods_to_be_deleted("app=seaweedfs", self.args.namespace):
            raise Exception("Timed out waiting for SeaweedFS pods to terminate")
        self._delete_if_exists(
            self.core_api.delete_namespaced_persistent_volume_claim,
            "seaweedfs-pvc",
            self.args.namespace,
        )
        self._wait_for_resource_absent(
            self.core_api.read_namespaced_persistent_volume_claim,
            "seaweedfs PVC",
            "seaweedfs-pvc",
            self.args.namespace,
        )

        # Delete existing job if it exists (jobs are immutable)
        print("🧹 Cleaning up existing SeaweedFS job...")
        self._delete_if_exists(
            self.batch_api.delete_namespaced_job,
            "init-seaweedfs",
            self.args.namespace,
        )
        self._wait_for_resource_absent(
            self.batch_api.read_namespaced_job,
            "init-seaweedfs job",
            "init-seaweedfs",
            self.args.namespace,
        )
        if not self._wait_for_pods_to_be_deleted("job-name=init-seaweedfs", self.args.namespace):
            raise Exception("Timed out waiting for init-seaweedfs pods to terminate")

        # Create the s3config Secret with actual credentials before the
        # Deployment so the volume is ready when SeaweedFS starts.
        print("📋 Creating seaweedfs-s3config Secret with runtime credentials")
        s3_config = json.dumps({
            "identities": [{
                "name": self.args.s3_access_key,
                "credentials": [{
                    "accessKey": self.args.s3_access_key,
                    "secretKey": self.args.s3_secret_key,
                }],
                "actions": ["Admin"],
            }]
        })
        self._apply_secret("seaweedfs-s3config", {"s3.json": s3_config})

        # Build and apply manifests, filtering out Secrets that deploy.py manages
        # directly (seaweedfs-s3config and aws-credentials). Without this filter
        # the kustomize base defaults would overwrite caller-supplied credentials.
        print("📋 Deploying SeaweedFS")
        substitutions = {
            "$NAMESPACE": self.args.namespace,
            "$APPLICATION_CRD_ID": "mlflow-pipelines",
            "$PROFILE_NAMESPACE_LABEL": "mlflow-profile",
            "$S3_BUCKET": self.args.s3_bucket,
        }
        manifests = self._build_kustomize(seaweedfs_path)
        for var, val in substitutions.items():
            manifests = manifests.replace(var, val)

        managed_secrets = {"seaweedfs-s3config", "aws-credentials"}
        manifest_docs = list(yaml.safe_load_all(manifests))
        manifests = "\n---\n".join(
            yaml.safe_dump(doc, default_flow_style=False, sort_keys=False).rstrip()
            for doc in manifest_docs if doc and not (
                isinstance(doc, dict)
                and doc.get("kind") == "Secret"
                and doc.get("metadata", {}).get("name") in managed_secrets
            )
        ) + "\n"
        self._apply_manifest_stream(manifests)

        # Wait for SeaweedFS to be ready
        try:
            # First wait for deployment to be created
            self.wait_for_deployment_to_exist("seaweedfs", self.args.namespace)

            # Then wait for it to become available
            self._wait_for_deployment_available("seaweedfs", self.args.namespace)
        except Exception as e:
            print(f"❌ SeaweedFS deployment failed to become ready: {e}")
            self.debug_deployment("seaweedfs", self.args.namespace)
            raise

        # Wait for the init job to complete (increased timeout for SeaweedFS startup)
        try:
            self._wait_for_job_completion("init-seaweedfs", self.args.namespace)
        except Exception as e:
            print(f"❌ SeaweedFS initialization job failed to complete: {e}")
            # Debug the init job (though it's a job, not a deployment with pods using app= label)
            # We'll check the job status specifically
            try:
                print("🔍 Debugging SeaweedFS initialization job...")
                job_status = self._read_optional(
                    self.batch_api.read_namespaced_job,
                    "init-seaweedfs",
                    self.args.namespace,
                )
                if job_status:
                    print(f"📋 SeaweedFS init job status:")
                    print(yaml.safe_dump(
                        self._serialize_k8s_object(job_status),
                        default_flow_style=False,
                        sort_keys=False,
                    ))
                else:
                    print("❌ No job status available or job not found")

                # Also try to get pod logs for the job using multiple label selectors
                print("🔍 Looking for SeaweedFS job pods...")
                job_pods = self.get_pods_with_label_selector("job-name=init-seaweedfs", self.args.namespace)

                # If that doesn't work, try alternative selectors
                if not job_pods:
                    print("🔍 Trying alternative label selector...")
                    job_pods = self.get_pods_with_label_selector("controller-uid", self.args.namespace)
                    # Filter pods that contain 'seaweedfs' in the name
                    job_pods = [pod for pod in job_pods if 'seaweedfs' in pod]

                if job_pods:
                    print(f"📋 Found {len(job_pods)} SeaweedFS job pod(s)")
                    for pod_name in job_pods:
                        print(f"📋 SeaweedFS init job pod logs for {pod_name}:")
                        logs = self.get_pod_logs(pod_name, self.args.namespace)
                        if logs:
                            print(logs)
                        else:
                            print("❌ No logs available for this pod")
                else:
                    print("❌ No SeaweedFS job pods found")

                # Additional debugging for SeaweedFS service issues
                print("🔍 Debugging SeaweedFS service and deployment...")

                # Check if minio-service exists and get its endpoints
                print("📋 Checking minio-service:")
                minio_svc = self._read_optional(
                    self.core_api.read_namespaced_service,
                    "minio-service",
                    self.args.namespace,
                )
                if minio_svc:
                    print(yaml.safe_dump(
                        self._serialize_k8s_object(minio_svc),
                        default_flow_style=False,
                        sort_keys=False,
                    ))
                else:
                    print("❌ minio-service not found")

                # Check if service endpoints are populated
                print("📋 Checking minio-service endpoints:")
                minio_endpoints = self._read_optional(
                    self.core_api.read_namespaced_endpoints,
                    "minio-service",
                    self.args.namespace,
                )
                if minio_endpoints:
                    print(yaml.safe_dump(
                        self._serialize_k8s_object(minio_endpoints),
                        default_flow_style=False,
                        sort_keys=False,
                    ))
                else:
                    print("❌ minio-service endpoints not found")

                # Check seaweedfs service endpoints too
                print("📋 Checking seaweedfs service endpoints:")
                seaweed_endpoints = self._read_optional(
                    self.core_api.read_namespaced_endpoints,
                    "seaweedfs",
                    self.args.namespace,
                )
                if seaweed_endpoints:
                    print(yaml.safe_dump(
                        self._serialize_k8s_object(seaweed_endpoints),
                        default_flow_style=False,
                        sort_keys=False,
                    ))
                else:
                    print("❌ seaweedfs service endpoints not found")

                # Check SeaweedFS pod status and logs
                print("📋 Checking SeaweedFS pods:")
                seaweedfs_pods = self.get_pods_for_deployment("seaweedfs", self.args.namespace)
                if seaweedfs_pods:
                    for pod_name in seaweedfs_pods:
                        print(f"📋 SeaweedFS pod {pod_name} status:")
                        pod_status = self._read_optional(
                            self.core_api.read_namespaced_pod,
                            pod_name,
                            self.args.namespace,
                        )
                        if pod_status:
                            print(yaml.safe_dump(
                                self._serialize_k8s_object(pod_status),
                                default_flow_style=False,
                                sort_keys=False,
                            ))

                        # Get SeaweedFS logs
                        print(f"📋 SeaweedFS pod {pod_name} logs:")
                        pod_logs = self.get_pod_logs(pod_name, self.args.namespace)
                        if pod_logs:
                            print(pod_logs)

                        # Check readiness probe status for this pod
                        print(f"📋 Checking readiness probe events for {pod_name}:")
                        pod_events = self.get_pod_events(pod_name, self.args.namespace)
                        if pod_events:
                            print(pod_events)

                        # Test direct pod connectivity (bypass service)
                        print(f"📋 Testing direct pod connectivity for {pod_name}:")
                        pod_ip = pod_status.status.pod_ip if pod_status and pod_status.status else ""
                        if pod_ip:
                            print(f"Pod IP: {pod_ip}")
                            direct_test = self.run_command(
                                [
                                    "kubectl", "run", "debug-pod-direct", "--rm", "-i",
                                    "--restart=Never", "--image=curlimages/curl",
                                    "-n", self.args.namespace, "--",
                                    "curl", "-v", "--connect-timeout", "10",
                                    f"http://{pod_ip}:8333/",
                                ],
                                check=False, capture_output=True, timeout=60
                            )
                            if direct_test and direct_test.stdout:
                                print(f"📋 Direct pod connectivity test result:")
                                print(direct_test.stdout)

                # Try to manually test the minio-service endpoint
                print("🔍 Testing minio-service endpoint manually:")
                endpoint_test = self.run_command(
                    [
                        "kubectl", "run", "test-pod", "--rm", "-i", "--restart=Never",
                        "--image=alpine/curl:latest", "-n", self.args.namespace, "--",
                        "curl", "-v", "--connect-timeout", "10",
                        f"http://minio-service.{self.args.namespace}:9000/",
                    ],
                    check=False, capture_output=True, timeout=60
                )
                if endpoint_test and endpoint_test.stdout:
                    print(f"📋 Endpoint test result:")
                    print(endpoint_test.stdout)

                # Test service connectivity using the cluster DNS
                print("🔍 Testing cluster DNS resolution:")
                dns_test = self.run_command(
                    [
                        "kubectl", "run", "dns-test", "--rm", "-i", "--restart=Never",
                        "--image=busybox", "-n", self.args.namespace, "--",
                        "nslookup", f"minio-service.{self.args.namespace}.svc.cluster.local",
                    ],
                    check=False, capture_output=True, timeout=60
                )
                if dns_test and dns_test.stdout:
                    print(f"📋 DNS resolution test result:")
                    print(dns_test.stdout)

                # List all pods in the namespace to see what's available
                print("🔍 Listing all pods in namespace for debugging:")
                all_pods = self.core_api.list_namespaced_pod(self.args.namespace).items
                if all_pods:
                    print(yaml.safe_dump(
                        [self._serialize_k8s_object(pod) for pod in all_pods],
                        default_flow_style=False,
                        sort_keys=False,
                    ))
            except Exception as debug_e:
                print(f"❌ Failed to debug SeaweedFS init job: {debug_e}")
            raise

    def deploy_postgres(self):
        """Deploy PostgreSQL for database storage"""
        print("🐘 Deploying PostgreSQL...")

        if self.args.postgres_tls:
            self._create_tls_secret(
                "postgres-tls-certs", cn="postgres-service",
                cert_filename="server.crt", key_filename="server.key",
                extra_sans=[
                    f"DNS:postgres-service.{self.args.namespace}",
                    f"DNS:postgres-service.{self.args.namespace}.svc",
                    f"DNS:postgres-service.{self.args.namespace}.svc.cluster.local",
                ],
            )
            platform_overlay = "openshift-tls" if self.args.platform == "openshift" else "tls"
        else:
            platform_overlay = "openshift" if self.args.platform == "openshift" else "base"
        postgres_path = self.repo_root / "config" / "postgres" / platform_overlay
        postgres_params_env = self.repo_root / "config" / "postgres" / "base" / "params.env"

        self.run_command([
            "sed", "-i", f"s#POSTGRES_IMAGE=.*#POSTGRES_IMAGE={self.args.postgres_image}#",
            str(postgres_params_env),
        ], f"Setting postgres image to {self.args.postgres_image}")

        # Note: PostgreSQL overlay doesn't use namespace parameter, so we apply directly to target namespace
        manifests = self._build_kustomize(postgres_path)
        self._apply_manifest_stream(manifests, namespace=self.args.namespace)

        # Wait for PostgreSQL to be ready
        try:
            # First wait for deployment to be created
            self.wait_for_deployment_to_exist("postgres-deployment", self.args.namespace)

            # Then wait for it to become available
            self._wait_for_deployment_available("postgres-deployment", self.args.namespace)
        except Exception as e:
            print(f"❌ PostgreSQL deployment failed to become ready: {e}")
            self.debug_deployment("postgres-deployment", self.args.namespace)
            raise

    def deploy_mlflow(self):
        """Create MLflow Custom Resource with configured options"""
        print("📝 Creating MLflow Custom Resource...")

        # Determine storage configuration
        use_postgres_backend = self.args.backend_store == "postgres"
        use_postgres_registry = self.args.registry_store == "postgres"
        use_s3_artifacts = self.args.artifact_storage in ("s3", "externals3")

        # Nothing to wait for here — _setup_tls_ca_bundle (called below, after all
        # infra certs are gathered) handles the propagation wait internally.

        # Base CR structure
        mlflow_cr = {
            "apiVersion": "mlflow.opendatahub.io/v1",
            "kind": "MLflow",
            "metadata": {
                "name": "mlflow",
                "namespace": self.args.namespace
            },
            "spec": {
                "image": {
                    "image": self.args.mlflow_image,
                    "imagePullPolicy": "Always"
                }
            }
        }

        # Configure backend store
        if use_postgres_backend:
            mlflow_cr["spec"]["backendStoreUriFrom"] = {
                "name": "mlflow-db-credentials",
                "key": "backend-store-uri"
            }
        else:
            mlflow_cr["spec"]["backendStoreUri"] = self.args.backend_store_uri

        # Configure registry store
        if use_postgres_registry:
            mlflow_cr["spec"]["registryStoreUriFrom"] = {
                "name": "mlflow-db-credentials",
                "key": "registry-store-uri"
            }
        else:
            mlflow_cr["spec"]["registryStoreUri"] = self.args.registry_store_uri

        # Configure artifact storage
        if use_s3_artifacts:
            # For S3 storage, use original serve_artifacts setting
            mlflow_cr["spec"]["serveArtifacts"] = str(self.args.serve_artifacts).lower() == "true"

            s3_destination = f"s3://{self.args.s3_bucket}/artifacts"
            mlflow_cr["spec"]["artifactsDestination"] = s3_destination

            # Set defaultArtifactRoot when not serving artifacts
            if self.args.serve_artifacts == "false":
                mlflow_cr["spec"]["defaultArtifactRoot"] = f"s3://{self.args.s3_bucket}/artifacts/runs"

            # Add S3 environment variables
            mlflow_cr["spec"]["envFrom"] = [{
                "secretRef": {"name": "aws-credentials"}
            }]
            # For in-cluster s3 (SeaweedFS) the endpoint is always set.
            # For externals3 only inject the endpoint override when explicitly provided;
            # real AWS endpoints must not be overridden.
            if self.args.s3_endpoint:
                mlflow_cr["spec"].setdefault("env", []).append(
                    {"name": "MLFLOW_S3_ENDPOINT_URL", "value": self.args.s3_endpoint}
                )
        else:
            # File-based artifact storage
            # IMPORTANT: MLflow operator validation requires serveArtifacts=true when using file-based storage
            if self.args.artifacts_destination.startswith("file://"):
                # Force serveArtifacts to true for file-based storage to pass operator validation
                if self.args.serve_artifacts == "false":
                    print("⚠️  Warning: Forcing serveArtifacts=true because file-based storage requires it")
                    mlflow_cr["spec"]["serveArtifacts"] = True
                    # Don't set defaultArtifactRoot when serving artifacts from file storage
                else:
                    mlflow_cr["spec"]["serveArtifacts"] = True
            else:
                # Non-file storage (e.g., hdfs://, etc.) - use original serve_artifacts value
                mlflow_cr["spec"]["serveArtifacts"] = str(self.args.serve_artifacts).lower() == "true"

            mlflow_cr["spec"]["artifactsDestination"] = self.args.artifacts_destination

            # Only set defaultArtifactRoot when not serving artifacts AND not using file:// storage
            if (self.args.serve_artifacts == "false" and
                not self.args.artifacts_destination.startswith("file://")):
                # For non-file storage, defaultArtifactRoot should be a subdirectory
                mlflow_cr["spec"]["defaultArtifactRoot"] = f"{self.args.artifacts_destination}/runs"

        selector = (self.args.workspace_label_selector or "").strip()
        if selector:
            try:
                selector_obj = json.loads(selector)

                if not isinstance(selector_obj, dict):
                    raise ValueError("workspace label selector must deserialize to an object")

                match_labels = selector_obj.get("matchLabels") or {}
                match_expressions = selector_obj.get("matchExpressions") or []
                if not isinstance(match_labels, dict):
                    raise ValueError("matchLabels must be an object")
                if not isinstance(match_expressions, list):
                    raise ValueError("matchExpressions must be a list")
                if not match_labels and not match_expressions:
                    raise ValueError(
                        "workspace label selector must include non-empty matchLabels or matchExpressions"
                    )

                mlflow_cr["spec"]["workspaceLabelSelector"] = selector_obj
            except Exception as e:
                raise ValueError(f"invalid --workspace-label-selector: {e}") from e

        # Add storage for local file/sqlite backends
        if not use_postgres_backend or not use_postgres_registry or not use_s3_artifacts:
            mlflow_cr["spec"]["storage"] = {
                "accessModes": ["ReadWriteOnce"],
                "resources": {
                    "requests": {
                        "storage": "10Gi"
                    }
                }
            }

        # Set up the CA trust anchor for TLS endpoints.  Three paths:
        #  1. Caller supplied --ca-bundle-configmap: use it directly.
        #  2. Caller supplied --ca-bundle-path: create a ConfigMap from the file.
        #  3. Self-signed certs generated above: combine into a bundle ConfigMap.
        if self.args.ca_bundle_configmap:
            self._tls_ca_bundle_cm = self.args.ca_bundle_configmap
        elif self.args.ca_bundle_path:
            with open(self.args.ca_bundle_path) as f:
                ca_pem = f.read()
            self._create_infra_ca_bundle(ca_pem)
        elif self._ca_cert_pem:
            self._create_infra_ca_bundle(self._ca_cert_pem)

        if self._tls_ca_bundle_cm:
            mlflow_cr["spec"]["caBundleConfigMap"] = {"name": self._tls_ca_bundle_cm}

        print("Generated MLflow CR:")
        print(yaml.dump(mlflow_cr, default_flow_style=False))

        # Apply the CR
        self._apply_mlflow_cr(mlflow_cr)

        # Wait for MLflow deployment to be created first
        try:
            self.wait_for_deployment_to_exist("mlflow", self.args.namespace)
        except Exception as e:
            print(f"❌ MLflow deployment creation failed: {e}")
            self._debug_operator_logs()
            raise

        # Then wait for MLflow to be available
        try:
            self._wait_for_deployment_available("mlflow", self.args.namespace)
        except Exception as e:
            print(f"❌ MLflow deployment failed to become available: {e}")
            self._debug_mlflow_deployment()
            raise

    def _debug_operator_logs(self):
        """Debug MLflow operator when deployment creation fails"""
        print("🔍 Debugging MLflow operator...")

        # Debug the operator deployment
        self.debug_deployment("mlflow-operator-controller-manager", self.args.namespace)

        # Also debug operator pods specifically using their label selector
        print("\n🔍 Debugging operator pods with label selector...")
        pod_names = self.get_pods_with_label_selector("control-plane=controller-manager", self.args.namespace)

        if pod_names:
            for pod_name in pod_names:
                print(f"\n🔍 Debugging operator pod: {pod_name}")

                # Get pod description
                try:
                    pod_description = self._read_optional(
                        self.core_api.read_namespaced_pod,
                        pod_name,
                        self.args.namespace,
                    )
                    if pod_description:
                        print(f"📋 Operator pod description for {pod_name}:")
                        print(yaml.safe_dump(
                            self._serialize_k8s_object(pod_description),
                            default_flow_style=False,
                            sort_keys=False,
                        ))
                except Exception as e:
                    print(f"❌ Failed to get operator pod description for {pod_name}: {e}")

                # Get logs if pod is ready for logs
                if self.is_pod_ready_for_logs(pod_name, self.args.namespace):
                    logs = self.get_pod_logs(pod_name, self.args.namespace)
                    if logs:
                        print(f"📋 Operator logs for {pod_name}:")
                        print(logs)
        else:
            print("❌ No operator pods found with control-plane=controller-manager label")

        # Check MLflow CR status (specific to operator debugging)
        try:
            print("\n📋 MLflow CR status:")
            cr_status = self._get_mlflow_cr()
            if cr_status:
                print(yaml.safe_dump(cr_status, default_flow_style=False, sort_keys=False))
            else:
                print("No MLflow CR found")
        except Exception as e:
            print(f"❌ Failed to get MLflow CR status: {e}")

    def _debug_mlflow_deployment(self):
        """Debug MLflow deployment when it fails to become available"""
        print("🔍 Debugging MLflow deployment...")

        # Use the common debug deployment method for MLflow
        self.debug_deployment("mlflow", self.args.namespace)

    def setup_port_forward(self):
        """Setup port forwarding for MLflow service"""
        print("🌐 Setting up port forwarding...")

        # Check if service exists
        service = self._read_optional(
            self.core_api.read_namespaced_service,
            "mlflow",
            self.args.namespace,
        )
        if not service:
            print("❌ MLflow service not found")
            return

        print("🎯 MLflow is ready! To access it, run the following command:")
        print(f"   kubectl port-forward service/mlflow 8080:5000 -n {self.args.namespace}")
        print("   Then visit: http://localhost:8080")


    def get_pods_for_deployment(self, deployment_name, namespace):
        """Get pod names for a given deployment"""
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=self._deployment_label_selector(deployment_name, namespace),
            ).items
            return [pod.metadata.name for pod in pods]
        except Exception as e:
            print(f"❌ Failed to get pods for deployment {deployment_name}: {e}")
            return []

    def get_pods_with_label_selector(self, label_selector, namespace):
        """Get pod names for a given label selector"""
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            ).items
            return [pod.metadata.name for pod in pods]
        except Exception as e:
            print(f"❌ Failed to get pods with selector {label_selector}: {e}")
            return []

    def get_pod_events(self, pod_name, namespace):
        """Get events for a specific pod"""
        try:
            events = self.core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
            ).items
            if not events:
                return "No events found"
            events.sort(
                key=lambda event: (
                    (
                        event.last_timestamp
                        or event.event_time
                        or event.metadata.creation_timestamp
                    ).timestamp()
                    if (
                        event.last_timestamp
                        or event.event_time
                        or event.metadata.creation_timestamp
                    )
                    else 0
                )
            )
            return yaml.safe_dump(
                [self._serialize_k8s_object(event) for event in events],
                default_flow_style=False,
                sort_keys=False,
            )
        except Exception as e:
            print(f"❌ Failed to get events for pod {pod_name}: {e}")
            return None

    def get_pod_logs(self, pod_name, namespace):
        """Get logs for a specific pod"""
        try:
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=100,
            )
            return logs or "No logs available"
        except ApiException as exc:
            if exc.status in {400, 404}:
                return None
            print(f"❌ Failed to get logs for pod {pod_name}: {exc}")
            return None
        except Exception as e:
            print(f"❌ Failed to get logs for pod {pod_name}: {e}")
            return None

    def is_pod_ready_for_logs(self, pod_name, namespace):
        """Check if pod is in a state where logs can be retrieved"""
        try:
            # Pods in Running, Succeeded, or Failed phases can have logs
            status_value = self._read_pod_phase(pod_name, namespace)
            return status_value in ["Running", "Succeeded", "Failed"]
        except Exception as e:
            print(f"❌ Failed to check pod status for {pod_name}: {e}")
            return False

    def wait_for_deployment_to_exist(self, deployment_name, namespace, timeout=300, poll_interval=10):
        """Wait for a deployment to exist with polling"""
        print(f"⏳ Waiting for {deployment_name} deployment to be created...")
        elapsed_time = 0

        while elapsed_time < timeout:
            result = self._read_optional(
                self.apps_api.read_namespaced_deployment,
                deployment_name,
                namespace,
            )
            if result:
                print(f"✅ {deployment_name} deployment created successfully")
                return True

            print(f"⏳ Waiting for {deployment_name} deployment to be created... ({elapsed_time}/{timeout}s)")
            time.sleep(poll_interval)
            elapsed_time += poll_interval

        # Timeout reached
        raise Exception(f"Timeout waiting for {deployment_name} deployment to be created after {timeout}s")

    def debug_deployment(self, deployment_name, namespace):
        """Debug a deployment by checking pods, events, and logs"""
        print(f"🔍 Debugging deployment '{deployment_name}' in namespace '{namespace}'...")

        # Check deployment status
        try:
            print(f"📋 Deployment {deployment_name} status:")
            deployment_status = self._read_optional(
                self.apps_api.read_namespaced_deployment,
                deployment_name,
                namespace,
            )
            if deployment_status:
                print(yaml.safe_dump(
                    self._serialize_k8s_object(deployment_status),
                    default_flow_style=False,
                    sort_keys=False,
                ))
            else:
                print("No deployment status available")
        except Exception as e:
            print(f"❌ Failed to get deployment status: {e}")

        # Get pods for the deployment
        pod_names = self.get_pods_for_deployment(deployment_name, namespace)

        if not pod_names:
            print(f"❌ No pods found for deployment {deployment_name}")
            return

        print(f"📋 Found {len(pod_names)} pod(s) for deployment {deployment_name}")

        # Debug each pod
        for pod_name in pod_names:
            print(f"\n🔍 Debugging pod: {pod_name}")

            # Get pod description
            try:
                pod_description = self._read_optional(
                    self.core_api.read_namespaced_pod,
                    pod_name,
                    namespace,
                )
                if pod_description:
                    print(f"📋 Pod description for {pod_name}:")
                    print(yaml.safe_dump(
                        self._serialize_k8s_object(pod_description),
                        default_flow_style=False,
                        sort_keys=False,
                    ))
            except Exception as e:
                print(f"❌ Failed to get pod description for {pod_name}: {e}")

            # Check if pod is failing by looking at its status
            try:
                status_value = self._read_pod_phase(pod_name, namespace)
                if status_value in ["Failed", "Pending"]:
                    print(f"⚠️  Pod {pod_name} is in {status_value} state - getting events")
                    events = self.get_pod_events(pod_name, namespace)
                    if events:
                        print(f"📋 Events for {pod_name}:")
                        print(events)
            except Exception as e:
                print(f"❌ Failed to check pod status for {pod_name}: {e}")

            # Get logs if pod is ready for logs
            if self.is_pod_ready_for_logs(pod_name, namespace):
                logs = self.get_pod_logs(pod_name, namespace)
                if logs:
                    print(f"📋 Logs for {pod_name}:")
                    print(logs)
            else:
                print(f"⚠️  Pod {pod_name} is not ready for log retrieval yet")

    def _validate_args(self):
        if self.args.artifact_storage == "externals3":
            missing = []
            if not self.args.s3_access_key:
                missing.append("--s3-access-key")
            if not self.args.s3_secret_key:
                missing.append("--s3-secret-key")
            if not self.args.s3_bucket:
                missing.append("--s3-bucket")
            if missing:
                raise ValueError(f"externals3 requires {', '.join(missing)} to be set")

        if self.args.ca_bundle_path and self.args.ca_bundle_configmap:
            raise ValueError("--ca-bundle-path and --ca-bundle-configmap are mutually exclusive")

        if self.args.skip_infrastructure:
            tls_flags = []
            if self.args.postgres_tls:
                tls_flags.append("--postgres-tls")
            if self.args.seaweedfs_tls:
                tls_flags.append("--seaweedfs-tls")
            has_ca = self.args.ca_bundle_path or self.args.ca_bundle_configmap
            if tls_flags and not has_ca:
                raise ValueError(
                    f"--skip-infrastructure with {', '.join(tls_flags)} requires "
                    "--ca-bundle-path or --ca-bundle-configmap"
                )

    def deploy(self):
        """Main deployment orchestration"""
        self._validate_args()
        print("🚀 Starting MLflow deployment on Kubernetes cluster...")
        print(f"Configuration:")
        print(f"  Namespace: {self.args.namespace}")
        print(f"  Backend Store: {self.args.backend_store}")
        print(f"  Registry Store: {self.args.registry_store}")
        print(f"  Artifact Storage: {self.args.artifact_storage}")
        print(f"  Serve Artifacts: {self.args.serve_artifacts}")
        print()

        # Write all GitHub Actions outputs immediately so they're available even if
        # deployment fails (e.g. for log collection in downstream steps).
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.getenv('GITHUB_OUTPUT'), 'a') as f:
                f.write(f"namespace={self.args.namespace}\n")
                f.write("mlflow_url=http://localhost:8080\n")
                f.write(f"s3_endpoint={self.args.s3_endpoint}\n")

        try:
            # Step 1: Create namespace
            self.create_namespace()

            # Step 2: Deploy dependencies based on configuration
            if self.args.backend_store == "postgres" or self.args.registry_store == "postgres":
                if not self.args.skip_infrastructure:
                    self.deploy_postgres()
                else:
                    print("⏭️  Skipping PostgreSQL deployment (--skip-infrastructure set)")
                self.create_postgres_secret()

            if self.args.artifact_storage in ("s3", "externals3"):
                self.create_s3_secret()
                if self.args.artifact_storage == "externals3":
                    print("⏭️  Skipping SeaweedFS deployment (externals3 uses caller-supplied S3)")
                elif not self.args.skip_infrastructure:
                    self.deploy_seaweedfs()
                else:
                    print("⏭️  Skipping SeaweedFS deployment (--skip-infrastructure set)")

            # Step 3: Deploy MLflow operator (skipped when operator is already installed)
            if not self.args.skip_operator:
                self.deploy_mlflow_operator()
            else:
                print("⏭️  Skipping MLflow operator deployment (--skip-operator set)")

            # Step 4: Create MLflow CR
            self.deploy_mlflow()

            # Step 5: Setup port forwarding info
            self.setup_port_forward()

            print("✅ MLflow deployment completed successfully!")

        except Exception as e:
            print(f"❌ Deployment failed: {e}")
            sys.exit(1)
        finally:
            # Remove generated private key from working tree regardless of outcome.
            kind_overlay = self.repo_root / "config" / "overlays" / "kind"
            for fname in ("tls.crt", "tls.key"):
                fpath = kind_overlay / fname
                if fpath.exists():
                    fpath.unlink()
            # Clean up the CA temp directory (contains CA private key).
            if self._ca_tmpdir:
                self._ca_tmpdir.cleanup()
                self._ca_tmpdir = None
                print("🗑️  Removed CA temp directory")


def main():
    parser = argparse.ArgumentParser(description="Deploy MLflow on Kubernetes cluster")
    parser.add_argument("--cleanup-tls", action="store_true", default=False,
                        help="Remove TLS resources created by a prior deployment and exit. "
                             "Only --namespace, --postgres-tls, and --seaweedfs-tls are "
                             "used; all other arguments are ignored. Resources from "
                             "--ca-bundle-configmap are externally managed and not cleaned up.")

    # Basic configuration
    parser.add_argument("--namespace", default="mlflow",
                       help="Kubernetes namespace")
    parser.add_argument("--mlflow-image", required=False,
                       help="Full MLflow image name and tag")
    parser.add_argument("--mlflow-operator-image", default="quay.io/opendatahub/mlflow-operator:odh-stable",
                       help="Full MLflow operator image name and tag")
    parser.add_argument("--skip-operator", action="store_true", default=False,
                       help="Skip deploying the MLflow operator (assume it is already installed)")
    parser.add_argument("--skip-infrastructure", action="store_true", default=False,
                       help="Skip deploying PostgreSQL and SeaweedFS (assume they are pre-existing); "
                            "credentials secrets are still created")

    # Storage configuration
    parser.add_argument("--backend-store", choices=["sqlite", "postgres"],
                       default="sqlite", help="Backend store type")
    parser.add_argument("--registry-store", choices=["sqlite", "postgres"],
                       default="sqlite", help="Registry store type")
    parser.add_argument("--artifact-storage", choices=["file", "s3", "externals3"],
                       default="file",
                       help="Artifact storage type: 'file' (local), 's3' (in-cluster SeaweedFS), "
                            "'externals3' (external S3 via caller-supplied AWS_* credentials, no SeaweedFS deployed)")
    parser.add_argument("--serve-artifacts", choices=["true", "false"],
                       default="true", help="Whether to serve artifacts")

    # Custom URIs
    parser.add_argument("--backend-store-uri", default="sqlite:////mlflow/mlflow.db")
    parser.add_argument("--registry-store-uri", default="sqlite:////mlflow/mlflow.db")
    parser.add_argument("--artifacts-destination", default="file:///mlflow/artifacts")

    # TLS flags — enable self-signed TLS for self-deployed infrastructure.
    # Postgres generates its own cert at container startup (no external deps).
    # SeaweedFS cert is generated on the host by this script via openssl and stored as a Secret.
    parser.add_argument("--postgres-tls", action="store_true", default=False,
                       help="Enable TLS on the self-deployed PostgreSQL server.")
    parser.add_argument("--seaweedfs-tls", action="store_true", default=False,
                       help="Enable TLS on the self-deployed SeaweedFS S3 endpoint.")

    # External CA bundle — use when connecting to pre-existing TLS-enabled storage
    # (i.e. --skip-infrastructure + --postgres-tls / --seaweedfs-tls).
    parser.add_argument("--ca-bundle-path", default="",
                       help="Path to a PEM CA bundle file. deploy.py creates a ConfigMap from it. "
                            "Mutually exclusive with --ca-bundle-configmap.")
    parser.add_argument("--ca-bundle-configmap", default="",
                       help="Name of an existing ConfigMap containing the CA bundle. "
                            "Mutually exclusive with --ca-bundle-path.")

    # Target platform — selects the appropriate kustomize overlay for infrastructure
    parser.add_argument("--platform", default="base", choices=["base", "openshift"],
                       help="Target platform (default: base). Selects the postgres/seaweedfs "
                            "overlay: 'base' uses the platform-agnostic overlay, "
                            "'openshift' uses the OpenShift overlay with platform-specific patches.")

    # Infrastructure images (override to avoid Docker Hub rate limits)
    parser.add_argument("--postgres-image", default="postgres:13",
                       help="PostgreSQL container image (default: postgres:13)")
    parser.add_argument("--seaweedfs-image", default="chrislusf/seaweedfs:4.07",
                       help="SeaweedFS container image (default: chrislusf/seaweedfs:4.07)")

    # PostgreSQL configuration
    parser.add_argument("--postgres-sslmode", default=None,
                       help="PostgreSQL sslmode appended to the connection URI "
                            "(e.g. disable, require, verify-full). "
                            "Default: 'verify-full' when --postgres-tls is set, 'disable' otherwise.")
    parser.add_argument("--postgres-host", default="")
    parser.add_argument("--postgres-port", default="5432")
    parser.add_argument("--postgres-user", default="postgres")
    parser.add_argument("--postgres-password", default="mysecretpassword")
    parser.add_argument("--postgres-backend-db", default="mydatabase")
    parser.add_argument("--postgres-registry-db", default="mydatabase")

    # S3 configuration
    parser.add_argument("--s3-bucket", default="")
    parser.add_argument("--s3-access-key", default="")
    parser.add_argument("--s3-secret-key", default="")
    parser.add_argument("--s3-endpoint", default="",
                       help="S3 endpoint URL override (e.g. for MinIO/SeaweedFS). "
                            "Omit for real AWS when using --artifact-storage externals3.")
    parser.add_argument("--s3-region", default="",
                       help="AWS region (optional; used when --artifact-storage externals3)")
    parser.add_argument("--workspace-label-selector", default="")

    args = parser.parse_args()

    # Apply SeaweedFS defaults for self-deployed S3 storage if not provided
    if args.artifact_storage == "s3":
        args.s3_bucket = args.s3_bucket or "mlpipeline"
        args.s3_access_key = args.s3_access_key or "minio"
        args.s3_secret_key = args.s3_secret_key or "minio123"

    deployer = MLflowDeployer(args)
    if args.cleanup_tls:
        deployer.cleanup_tls_certificates()
    else:
        deployer.deploy()


if __name__ == "__main__":
    main()