#!/usr/bin/env python3
"""
MLflow Deployment Script for Kind Clusters

This script deploys MLflow operator and creates an MLflow instance with configurable
storage backends (SQLite/PostgreSQL) and artifact storage (file/S3).
"""

import argparse
import subprocess
import yaml
import os
import sys
import time
from pathlib import Path
from typing import List, Union


class MLflowDeployer:
    def __init__(self, args):
        self.args = args
        self.repo_root = Path(__file__).parent.parent.parent.parent

        # Set default endpoints if not provided
        if not self.args.s3_endpoint:
            self.args.s3_endpoint = f"http://minio-service.{self.args.namespace}.svc.cluster.local:9000"

        if not self.args.postgres_host:
            self.args.postgres_host = f"postgres.{self.args.namespace}.svc.cluster.local"

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

            # Iterate over the process's stdout line by line in real time
            output_lines = []
            for line in process.stdout:
                # Only print output in real-time if not capturing silently
                if not capture_output:
                    print(
                        line, end='',
                        flush=True)  # Use flush=True to ensure immediate printing
                output_lines.append(line.rstrip())

            # Wait for the process to finish and get the return code
            return_code = process.wait(timeout=timeout)

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

    def create_namespace(self):
        """Create Kubernetes namespace"""
        print(f"🔨 Creating namespace '{self.args.namespace}'...")

        # Check if namespace already exists
        result = self.run_command(
            f"kubectl get namespace {self.args.namespace}",
            check=False, capture_output=True
        )

        if result and result.stdout and "Active" in result.stdout:
            print(f"✅ Namespace '{self.args.namespace}' already exists")
        else:
            # Namespace doesn't exist, create it
            self.run_command(
                f"kubectl create namespace {self.args.namespace}",
                f"Creating namespace {self.args.namespace}"
            )

    def generate_tls_certificates(self):
        """Generate TLS certificates for MLflow operator deployment"""
        print("🔐 Generating TLS certificates...")

        kind_overlay = self.repo_root / "config" / "overlays" / "kind"
        generate_tls_script = kind_overlay / "generate-tls.sh"

        # Ensure the script is executable
        self.run_command(f"chmod +x {generate_tls_script}", "Making TLS generation script executable")

        # Generate certificate and key files using the updated script
        self.run_command(
            f"{generate_tls_script} generate",
            "Generating TLS certificate and private key"
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
        """Clean up temporary TLS certificate files for security"""
        print("🧹 Cleaning up temporary TLS certificate files...")

        kind_overlay = self.repo_root / "config" / "overlays" / "kind"
        cert_file = kind_overlay / "tls.crt"
        key_file = kind_overlay / "tls.key"

        try:
            if cert_file.exists():
                cert_file.unlink()
                print(f"🗑️  Removed TLS certificate file: {cert_file}")

            if key_file.exists():
                key_file.unlink()
                print(f"🗑️  Removed TLS private key file: {key_file}")
        except Exception as e:
            print(f"⚠️  Warning: Failed to clean up TLS files: {e}")
            print("   Consider manually removing tls.crt and tls.key from config/overlays/kind/")

    def deploy_mlflow_operator(self):
        """Deploy MLflow operator using kustomize"""
        print("🚀 Deploying MLflow operator...")

        # Update base params.env to set the correct namespace for the operator
        base_params_env = self.repo_root / "config" / "base" / "params.env"
        print(f"📝 Updating operator namespace to '{self.args.namespace}' in {base_params_env}")

        self.run_command(
            f"sed -i 's/NAMESPACE=.*/NAMESPACE={self.args.namespace}/' {base_params_env}",
            f"Setting operator namespace to {self.args.namespace}"
        )

        self.run_command(
            f"sed -i 's|MLFLOW_OPERATOR_IMAGE=.*|MLFLOW_OPERATOR_IMAGE={self.args.mlflow_operator_image}|' {base_params_env}",
            f"Setting operator image to {self.args.mlflow_operator_image}"
        )

        # Generate TLS certificates before building with kustomize
        self.generate_tls_certificates()

        # Use the kind overlay with proper environment setup
        kind_overlay = self.repo_root / "config" / "overlays" / "kind"

        cmd = f"cd {self.repo_root} && export NAMESPACE={self.args.namespace} && kustomize build {kind_overlay} | envsubst | kubectl apply -f -"
        self.run_command(cmd, "Deploying MLflow operator")

        # Wait for operator to be ready (now in the correct namespace)
        print("⏳ Waiting for MLflow operator to be ready...")
        try:
            self.run_command(
                "kubectl wait --for=condition=available deployment/mlflow-operator-controller-manager "
                f"--timeout=300s -n {self.args.namespace}",
                "Waiting for operator deployment"
            )
        except Exception as e:
            print(f"❌ MLflow operator deployment failed to become ready: {e}")
            self.debug_deployment("mlflow-operator-controller-manager", self.args.namespace)
            raise

    def create_postgres_secret(self):
        """Create PostgreSQL credentials secret"""
        print("🔐 Creating PostgreSQL credentials secret...")

        backend_uri = f"postgresql://{self.args.postgres_user}:{self.args.postgres_password}@{self.args.postgres_host}:{self.args.postgres_port}/{self.args.postgres_backend_db}"
        registry_uri = f"postgresql://{self.args.postgres_user}:{self.args.postgres_password}@{self.args.postgres_host}:{self.args.postgres_port}/{self.args.postgres_registry_db}"

        # Delete existing secret if it exists
        self.run_command(
            f"kubectl delete secret mlflow-db-credentials -n {self.args.namespace}",
            check=False, capture_output=True
        )

        self.run_command(
            f"kubectl create secret generic mlflow-db-credentials "
            f"--from-literal=backend-store-uri='{backend_uri}' "
            f"--from-literal=registry-store-uri='{registry_uri}' "
            f"-n {self.args.namespace}",
            "Creating PostgreSQL credentials secret"
        )

    def create_s3_secret(self):
        """Create S3/AWS credentials secret"""
        print("🔐 Creating S3 credentials secret...")

        # Delete existing secret if it exists
        self.run_command(
            f"kubectl delete secret aws-credentials -n {self.args.namespace}",
            check=False, capture_output=True
        )

        self.run_command(
            f"kubectl create secret generic aws-credentials "
            f"--from-literal=AWS_ACCESS_KEY_ID='{self.args.s3_access_key}' "
            f"--from-literal=AWS_SECRET_ACCESS_KEY='{self.args.s3_secret_key}' "
            f"-n {self.args.namespace}",
            "Creating S3 credentials secret"
        )

    def deploy_seaweedfs(self):
        """Deploy SeaweedFS for S3-compatible storage"""
        print("🌊 Deploying SeaweedFS...")

        seaweedfs_path = self.repo_root / "config" / "overlays" / "kind" / "seaweedfs" / "base"

        # Note: base params.env namespace already updated by deploy_mlflow_operator()
        print(f"📝 SeaweedFS will deploy to namespace '{self.args.namespace}' (from base params.env)")

        # Delete existing job if it exists (jobs are immutable)
        print("🧹 Cleaning up existing SeaweedFS job...")
        self.run_command(
            f"kubectl delete job init-seaweedfs -n {self.args.namespace}",
            check=False, capture_output=True
        )

        # Export all environment variables needed for SeaweedFS
        cmd = (f"export NAMESPACE={self.args.namespace} "
               f"APPLICATION_CRD_ID=mlflow-pipelines "
               f"PROFILE_NAMESPACE_LABEL=mlflow-profile "
               f"S3_ADMIN_USER=kind-admin && "
               f"kustomize build {seaweedfs_path} | envsubst '$NAMESPACE,$APPLICATION_CRD_ID,$PROFILE_NAMESPACE_LABEL,$S3_ADMIN_USER' | kubectl apply -f -")
        self.run_command(cmd, "Deploying SeaweedFS")

        # Wait for SeaweedFS to be ready
        try:
            # First wait for deployment to be created
            self.wait_for_deployment_to_exist("seaweedfs", self.args.namespace)

            # Then wait for it to become available
            print("⏳ Waiting for SeaweedFS deployment to become available...")
            self.run_command(
                f"kubectl wait --for=condition=available deployment/seaweedfs "
                f"--timeout=300s -n {self.args.namespace}",
                "Waiting for SeaweedFS deployment to be available"
            )
        except Exception as e:
            print(f"❌ SeaweedFS deployment failed to become ready: {e}")
            self.debug_deployment("seaweedfs", self.args.namespace)
            raise

        # Wait for the init job to complete (increased timeout for SeaweedFS startup)
        print("⏳ Waiting for SeaweedFS initialization to complete...")
        try:
            self.run_command(
                f"kubectl wait --for=condition=complete job/init-seaweedfs "
                f"--timeout=400s -n {self.args.namespace}",
                "Waiting for SeaweedFS initialization job",
                timeout=400
            )
        except Exception as e:
            print(f"❌ SeaweedFS initialization job failed to complete: {e}")
            # Debug the init job (though it's a job, not a deployment with pods using app= label)
            # We'll check the job status specifically
            try:
                print("🔍 Debugging SeaweedFS initialization job...")
                job_status = self.run_command(
                    f"kubectl describe job init-seaweedfs -n {self.args.namespace}",
                    check=False, capture_output=True
                )
                if job_status and job_status.stdout:
                    print(f"📋 SeaweedFS init job status:")
                    print(job_status.stdout)
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
                minio_svc = self.run_command(
                    f"kubectl get service minio-service -n {self.args.namespace} -o wide",
                    check=False, capture_output=True
                )
                if minio_svc and minio_svc.stdout:
                    print(minio_svc.stdout)
                else:
                    print("❌ minio-service not found")

                # Check if service endpoints are populated
                print("📋 Checking minio-service endpoints:")
                minio_endpoints = self.run_command(
                    f"kubectl get endpoints minio-service -n {self.args.namespace} -o yaml",
                    check=False, capture_output=True
                )
                if minio_endpoints and minio_endpoints.stdout:
                    print(minio_endpoints.stdout)
                else:
                    print("❌ minio-service endpoints not found")

                # Check seaweedfs service endpoints too
                print("📋 Checking seaweedfs service endpoints:")
                seaweed_endpoints = self.run_command(
                    f"kubectl get endpoints seaweedfs -n {self.args.namespace} -o yaml",
                    check=False, capture_output=True
                )
                if seaweed_endpoints and seaweed_endpoints.stdout:
                    print(seaweed_endpoints.stdout)
                else:
                    print("❌ seaweedfs service endpoints not found")

                # Check SeaweedFS pod status and logs
                print("📋 Checking SeaweedFS pods:")
                seaweedfs_pods = self.get_pods_for_deployment("seaweedfs", self.args.namespace)
                if seaweedfs_pods:
                    for pod_name in seaweedfs_pods:
                        print(f"📋 SeaweedFS pod {pod_name} status:")
                        pod_status = self.run_command(
                            f"kubectl get pod {pod_name} -n {self.args.namespace} -o wide",
                            check=False, capture_output=True
                        )
                        if pod_status and pod_status.stdout:
                            print(pod_status.stdout)

                        # Get SeaweedFS logs
                        print(f"📋 SeaweedFS pod {pod_name} logs:")
                        pod_logs = self.get_pod_logs(pod_name, self.args.namespace)
                        if pod_logs:
                            print(pod_logs)

                        # Check readiness probe status for this pod
                        print(f"📋 Checking readiness probe events for {pod_name}:")
                        pod_events = self.run_command(
                            f"kubectl get events --field-selector involvedObject.name={pod_name} "
                            f"-n {self.args.namespace} --sort-by='.lastTimestamp'",
                            check=False, capture_output=True
                        )
                        if pod_events and pod_events.stdout:
                            print(pod_events.stdout)

                        # Test direct pod connectivity (bypass service)
                        print(f"📋 Testing direct pod connectivity for {pod_name}:")
                        pod_ip = self.run_command(
                            f"kubectl get pod {pod_name} -n {self.args.namespace} -o jsonpath='{{.status.podIP}}'",
                            check=False, capture_output=True
                        )
                        if pod_ip and pod_ip.stdout:
                            print(f"Pod IP: {pod_ip.stdout}")
                            direct_test = self.run_command(
                                f"kubectl run debug-pod-direct --rm -i --restart=Never "
                                f"--image=curlimages/curl -n {self.args.namespace} "
                                f"-- curl -v --connect-timeout 10 http://{pod_ip.stdout}:8333/",
                                check=False, capture_output=True, timeout=60
                            )
                            if direct_test and direct_test.stdout:
                                print(f"📋 Direct pod connectivity test result:")
                                print(direct_test.stdout)

                # Try to manually test the minio-service endpoint
                print("🔍 Testing minio-service endpoint manually:")
                endpoint_test = self.run_command(
                    f"kubectl run test-pod --rm -i --restart=Never "
                    f"--image=alpine/curl:latest -n {self.args.namespace} "
                    f"-- curl -v --connect-timeout 10 http://minio-service.{self.args.namespace}:9000/",
                    check=False, capture_output=True, timeout=60
                )
                if endpoint_test and endpoint_test.stdout:
                    print(f"📋 Endpoint test result:")
                    print(endpoint_test.stdout)

                # Test service connectivity using the cluster DNS
                print("🔍 Testing cluster DNS resolution:")
                dns_test = self.run_command(
                    f"kubectl run dns-test --rm -i --restart=Never "
                    f"--image=busybox -n {self.args.namespace} "
                    f"-- nslookup minio-service.{self.args.namespace}.svc.cluster.local",
                    check=False, capture_output=True, timeout=60
                )
                if dns_test and dns_test.stdout:
                    print(f"📋 DNS resolution test result:")
                    print(dns_test.stdout)

                # List all pods in the namespace to see what's available
                print("🔍 Listing all pods in namespace for debugging:")
                all_pods = self.run_command(
                    f"kubectl get pods -n {self.args.namespace}",
                    check=False, capture_output=True
                )
                if all_pods and all_pods.stdout:
                    print(all_pods.stdout)
            except Exception as debug_e:
                print(f"❌ Failed to debug SeaweedFS init job: {debug_e}")
            raise

    def deploy_postgres(self):
        """Deploy PostgreSQL for database storage"""
        print("🐘 Deploying PostgreSQL...")

        postgres_path = self.repo_root / "config" / "overlays" / "kind" / "postgres"

        # Note: PostgreSQL overlay doesn't use namespace parameter, so we apply directly to target namespace
        cmd = f"cd {postgres_path} && kustomize build . | kubectl apply -n {self.args.namespace} -f -"
        self.run_command(cmd, "Deploying PostgreSQL")

        # Wait for PostgreSQL to be ready
        try:
            # First wait for deployment to be created
            self.wait_for_deployment_to_exist("postgres-deployment", self.args.namespace)

            # Then wait for it to become available
            print("⏳ Waiting for PostgreSQL deployment to become available...")
            self.run_command(
                f"kubectl wait --for=condition=available deployment/postgres-deployment "
                f"--timeout=300s -n {self.args.namespace}",
                "Waiting for PostgreSQL deployment to be available"
            )
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
        use_s3_artifacts = self.args.artifact_storage == "s3"

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
                },
                "replicas": 1,
                "resources": {
                    "requests": {
                        "cpu": "250m",
                        "memory": "512Mi"
                    },
                    "limits": {
                        "cpu": "1",
                        "memory": "1Gi"
                    }
                },
                "serveArtifacts": self.args.serve_artifacts == "true"
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
            s3_destination = f"s3://{self.args.s3_bucket}/artifacts"
            mlflow_cr["spec"]["artifactsDestination"] = s3_destination

            # Set defaultArtifactRoot when not serving artifacts
            if self.args.serve_artifacts == "false":
                mlflow_cr["spec"]["defaultArtifactRoot"] = f"s3://{self.args.s3_bucket}/artifacts/runs"

            # Add S3 environment variables
            mlflow_cr["spec"]["envFrom"] = [{
                "secretRef": {"name": "aws-credentials"}
            }]
            mlflow_cr["spec"]["env"] = [
                {"name": "MLFLOW_S3_ENDPOINT_URL", "value": self.args.s3_endpoint}
            ]
        else:
            mlflow_cr["spec"]["artifactsDestination"] = self.args.artifacts_destination

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

        # Write CR to file
        cr_file = Path("/tmp/mlflow-cr.yaml")
        with open(cr_file, 'w') as f:
            yaml.dump(mlflow_cr, f, default_flow_style=False)

        print("Generated MLflow CR:")
        print(yaml.dump(mlflow_cr, default_flow_style=False))

        # Apply the CR
        self.run_command(f"kubectl apply -f {cr_file}", "Creating MLflow CR")

        # Wait for MLflow deployment to be created first
        try:
            self.wait_for_deployment_to_exist("mlflow", self.args.namespace)
        except Exception as e:
            print(f"❌ MLflow deployment creation failed: {e}")
            self._debug_operator_logs()
            raise

        # Then wait for MLflow to be available
        print("⏳ Waiting for MLflow to be available...")
        try:
            self.run_command(
                f"kubectl wait --for=condition=available deployment/mlflow "
                f"--timeout=300s -n {self.args.namespace}",
                "Waiting for MLflow deployment to be available"
            )
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
                    pod_description = self.run_command(
                        f"kubectl describe pod {pod_name} -n {self.args.namespace}",
                        check=False, capture_output=True
                    )
                    if pod_description and pod_description.stdout:
                        print(f"📋 Operator pod description for {pod_name}:")
                        print(pod_description.stdout)
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
            cr_status = self.run_command(
                f"kubectl describe mlflow mlflow -n {self.args.namespace}",
                check=False, capture_output=True
            )
            if cr_status and cr_status.stdout:
                print(cr_status.stdout)
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
        try:
            svc_output = self.run_command(
                f"kubectl get service mlflow -n {self.args.namespace} -o yaml",
                capture_output=True
            )
            if not svc_output:
                print("❌ MLflow service not found")
                return
        except:
            print("❌ MLflow service not found")
            return

        print("🎯 MLflow is ready! To access it, run the following command:")
        print(f"   kubectl port-forward service/mlflow 8080:5000 -n {self.args.namespace}")
        print("   Then visit: http://localhost:8080")

        # Set outputs for GitHub Actions
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.getenv('GITHUB_OUTPUT'), 'a') as f:
                f.write(f"mlflow_url=http://localhost:8080\n")
                f.write(f"namespace={self.args.namespace}\n")

    def get_pods_for_deployment(self, deployment_name, namespace):
        """Get pod names for a given deployment"""
        try:
            pod_names = self.run_command(
                f"kubectl get pods -l app={deployment_name} -n {namespace} "
                f"-o jsonpath='{{.items[*].metadata.name}}'",
                check=False, capture_output=True
            )
            return pod_names.stdout.split() if pod_names and pod_names.stdout else []
        except Exception as e:
            print(f"❌ Failed to get pods for deployment {deployment_name}: {e}")
            return []

    def get_pods_with_label_selector(self, label_selector, namespace):
        """Get pod names for a given label selector"""
        try:
            pod_names = self.run_command(
                f"kubectl get pods -l {label_selector} -n {namespace} "
                f"-o jsonpath='{{.items[*].metadata.name}}'",
                check=False, capture_output=True
            )
            return pod_names.stdout.split() if pod_names and pod_names.stdout else []
        except Exception as e:
            print(f"❌ Failed to get pods with selector {label_selector}: {e}")
            return []

    def get_pod_events(self, pod_name, namespace):
        """Get events for a specific pod"""
        try:
            events = self.run_command(
                f"kubectl get events --field-selector involvedObject.name={pod_name} "
                f"-n {namespace} --sort-by='.lastTimestamp'",
                check=False, capture_output=True
            )
            return events.stdout if events and events.stdout else "No events found"
        except Exception as e:
            print(f"❌ Failed to get events for pod {pod_name}: {e}")
            return None

    def get_pod_logs(self, pod_name, namespace):
        """Get logs for a specific pod"""
        try:
            logs = self.run_command(
                f"kubectl logs {pod_name} -n {namespace} --tail=100",
                check=False, capture_output=True
            )
            return logs.stdout if logs and logs.stdout else "No logs available"
        except Exception as e:
            print(f"❌ Failed to get logs for pod {pod_name}: {e}")
            return None

    def is_pod_ready_for_logs(self, pod_name, namespace):
        """Check if pod is in a state where logs can be retrieved"""
        try:
            pod_status = self.run_command(
                f"kubectl get pod {pod_name} -n {namespace} "
                f"-o jsonpath='{{.status.phase}}'",
                check=False, capture_output=True
            )
            # Pods in Running, Succeeded, or Failed phases can have logs
            status_value = pod_status.stdout if pod_status and pod_status.stdout else ""
            return status_value in ["Running", "Succeeded", "Failed"]
        except Exception as e:
            print(f"❌ Failed to check pod status for {pod_name}: {e}")
            return False

    def wait_for_deployment_to_exist(self, deployment_name, namespace, timeout=300, poll_interval=10):
        """Wait for a deployment to exist with polling"""
        print(f"⏳ Waiting for {deployment_name} deployment to be created...")
        elapsed_time = 0

        while elapsed_time < timeout:
            try:
                # Check if deployment exists
                result = self.run_command(
                    f"kubectl get deployment {deployment_name} -n {namespace}",
                    check=False, capture_output=True
                )
                if result and result.returncode == 0:  # Deployment exists
                    print(f"✅ {deployment_name} deployment created successfully")
                    return True
            except Exception:
                pass

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
            deployment_status = self.run_command(
                f"kubectl describe deployment {deployment_name} -n {namespace}",
                check=False, capture_output=True
            )
            if deployment_status and deployment_status.stdout:
                print(deployment_status.stdout)
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
                pod_description = self.run_command(
                    f"kubectl describe pod {pod_name} -n {namespace}",
                    check=False, capture_output=True
                )
                if pod_description and pod_description.stdout:
                    print(f"📋 Pod description for {pod_name}:")
                    print(pod_description.stdout)
            except Exception as e:
                print(f"❌ Failed to get pod description for {pod_name}: {e}")

            # Check if pod is failing by looking at its status
            try:
                pod_status = self.run_command(
                    f"kubectl get pod {pod_name} -n {namespace} "
                    f"-o jsonpath='{{.status.phase}}'",
                    check=False, capture_output=True
                )

                status_value = pod_status.stdout if pod_status and pod_status.stdout else ""
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

    def deploy(self):
        """Main deployment orchestration"""
        print("🚀 Starting MLflow deployment on Kind cluster...")
        print(f"Configuration:")
        print(f"  Namespace: {self.args.namespace}")
        print(f"  Backend Store: {self.args.backend_store}")
        print(f"  Registry Store: {self.args.registry_store}")
        print(f"  Artifact Storage: {self.args.artifact_storage}")
        print(f"  Serve Artifacts: {self.args.serve_artifacts}")
        print()

        try:
            # Step 1: Create namespace
            self.create_namespace()

            # Step 2: Deploy dependencies based on configuration
            if self.args.backend_store == "postgres" or self.args.registry_store == "postgres":
                self.deploy_postgres()
                self.create_postgres_secret()

            if self.args.artifact_storage == "s3":
                self.create_s3_secret()
                self.deploy_seaweedfs()

            # Step 3: Deploy MLflow operator
            self.deploy_mlflow_operator()

            # Step 4: Create MLflow CR
            self.deploy_mlflow()

            # Step 5: Setup port forwarding info
            self.setup_port_forward()

            print("✅ MLflow deployment completed successfully!")

        except Exception as e:
            print(f"❌ Deployment failed: {e}")
            sys.exit(1)
        finally:
            # Always clean up TLS files for security, regardless of success or failure
            self.cleanup_tls_certificates()


def main():
    parser = argparse.ArgumentParser(description="Deploy MLflow on Kind cluster")

    # Basic configuration
    parser.add_argument("--namespace", default="mlflow",
                       help="Kubernetes namespace")
    parser.add_argument("--mlflow-image", default="quay.io/opendatahub/mlflow:master",
                       help="Full MLflow image name and tag")
    parser.add_argument("--mlflow-operator-image", default="quay.io/opendatahub/mlflow-operator:main",
                       help="Full MLflow operator image name and tag")

    # Storage configuration
    parser.add_argument("--backend-store", choices=["sqlite", "postgres"],
                       default="sqlite", help="Backend store type")
    parser.add_argument("--registry-store", choices=["sqlite", "postgres"],
                       default="sqlite", help="Registry store type")
    parser.add_argument("--artifact-storage", choices=["file", "s3"],
                       default="file", help="Artifact storage type")
    parser.add_argument("--serve-artifacts", choices=["true", "false"],
                       default="true", help="Whether to serve artifacts")

    # Custom URIs
    parser.add_argument("--backend-store-uri", default="sqlite:////mlflow/mlflow.db")
    parser.add_argument("--registry-store-uri", default="sqlite:////mlflow/mlflow.db")
    parser.add_argument("--artifacts-destination", default="file:///mlflow/artifacts")

    # PostgreSQL configuration
    parser.add_argument("--postgres-host", default="")
    parser.add_argument("--postgres-port", default="5432")
    parser.add_argument("--postgres-user", default="mlflow")
    parser.add_argument("--postgres-password", default="password")
    parser.add_argument("--postgres-backend-db", default="mlflow")
    parser.add_argument("--postgres-registry-db", default="mlflow_registry")

    # S3 configuration
    parser.add_argument("--s3-bucket", default="mlpipeline")
    parser.add_argument("--s3-access-key", default="minio")
    parser.add_argument("--s3-secret-key", default="minio123")
    parser.add_argument("--s3-endpoint", default="")

    args = parser.parse_args()

    deployer = MLflowDeployer(args)
    deployer.deploy()


if __name__ == "__main__":
    main()