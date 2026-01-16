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

    def run_command(self, cmd, description=None, check=True, capture_output=False):
        """Execute shell command with error handling"""
        if description:
            print(f"📋 {description}")

        print(f"🔧 Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")

        try:
            if capture_output:
                result = subprocess.run(cmd, shell=isinstance(cmd, str),
                                      check=check, capture_output=True, text=True)
                return result.stdout.strip()
            else:
                subprocess.run(cmd, shell=isinstance(cmd, str), check=check)
                return None
        except subprocess.CalledProcessError as e:
            print(f"❌ Command failed: {e}")
            if capture_output and e.stdout:
                print(f"STDOUT: {e.stdout}")
            if capture_output and e.stderr:
                print(f"STDERR: {e.stderr}")
            if check:
                sys.exit(1)
            return None

    def create_namespace(self):
        """Create Kubernetes namespace"""
        print(f"🔨 Creating namespace '{self.args.namespace}'...")

        # Check if namespace already exists
        result = self.run_command(
            f"kubectl get namespace {self.args.namespace}",
            check=False, capture_output=True
        )

        if result and "Active" in result:
            print(f"✅ Namespace '{self.args.namespace}' already exists")
        else:
            # Namespace doesn't exist, create it
            self.run_command(
                f"kubectl create namespace {self.args.namespace}",
                f"Creating namespace {self.args.namespace}"
            )

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

        # Use the kind overlay with proper environment setup
        kind_overlay = self.repo_root / "config" / "overlays" / "kind"

        cmd = f"cd {self.repo_root} && export NAMESPACE={self.args.namespace} && kustomize build {kind_overlay} | envsubst | kubectl apply -f -"
        self.run_command(cmd, "Deploying MLflow operator")

        # Wait for operator to be ready (now in the correct namespace)
        print("⏳ Waiting for MLflow operator to be ready...")
        self.run_command(
            "kubectl wait --for=condition=available deployment/mlflow-operator-controller-manager "
            f"--timeout=300s -n {self.args.namespace}",
            "Waiting for operator deployment"
        )

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
        print("⏳ Waiting for SeaweedFS to be ready...")
        self.run_command(
            f"kubectl wait --for=condition=available deployment/seaweedfs "
            f"--timeout=300s -n {self.args.namespace}",
            "Waiting for SeaweedFS deployment"
        )

        # Wait for the init job to complete
        print("⏳ Waiting for SeaweedFS initialization to complete...")
        self.run_command(
            f"kubectl wait --for=condition=complete job/init-seaweedfs "
            f"--timeout=300s -n {self.args.namespace}",
            "Waiting for SeaweedFS initialization job"
        )

    def deploy_postgres(self):
        """Deploy PostgreSQL for database storage"""
        print("🐘 Deploying PostgreSQL...")

        postgres_path = self.repo_root / "config" / "overlays" / "kind" / "postgres"

        # Note: PostgreSQL overlay doesn't use namespace parameter, so we apply directly to target namespace
        cmd = f"cd {postgres_path} && kustomize build . | kubectl apply -n {self.args.namespace} -f -"
        self.run_command(cmd, "Deploying PostgreSQL")

        # Wait for PostgreSQL to be ready
        print("⏳ Waiting for PostgreSQL to be ready...")
        self.run_command(
            f"kubectl wait --for=condition=available deployment/postgres "
            f"--timeout=300s -n {self.args.namespace}",
            "Waiting for PostgreSQL deployment"
        )

    def determine_image_tag(self):
        """Determine MLflow image tag based on target branch"""
        if self.args.target_branch and self.args.target_branch != "master":
            return f"quay.io/opendatahub/mlflow:{self.args.target_branch}"
        else:
            return "quay.io/opendatahub/mlflow:master"

    def create_mlflow_cr(self):
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
                    "image": self.determine_image_tag(),
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
        print("⏳ Waiting for MLflow deployment to be created...")
        try:
            self.run_command(
                f"kubectl wait --for=jsonpath='{{.metadata.name}}=mlflow' deployment/mlflow "
                f"--timeout=300s -n {self.args.namespace}",
                "Waiting for MLflow deployment to exist"
            )
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

        # Get operator pod name
        try:
            operator_pods = self.run_command(
                f"kubectl get pods -l control-plane=controller-manager "
                f"-n {self.args.namespace} -o jsonpath='{{.items[*].metadata.name}}'",
                check=False, capture_output=True
            )

            if operator_pods:
                pod_names = operator_pods.split()
                for pod_name in pod_names:
                    print(f"📋 MLflow operator pod logs for {pod_name}:")
                    logs = self.run_command(
                        f"kubectl logs {pod_name} -n {self.args.namespace} --tail=100",
                        check=False, capture_output=True
                    )
                    if logs:
                        print(logs)
                    else:
                        print("No logs available")
            else:
                print("❌ No MLflow operator pods found")

        except Exception as e:
            print(f"❌ Failed to get operator logs: {e}")

        # Check MLflow CR status
        try:
            print("📋 MLflow CR status:")
            cr_status = self.run_command(
                f"kubectl describe mlflow mlflow -n {self.args.namespace}",
                check=False, capture_output=True
            )
            if cr_status:
                print(cr_status)
        except Exception as e:
            print(f"❌ Failed to get MLflow CR status: {e}")

    def _debug_mlflow_deployment(self):
        """Debug MLflow deployment when it fails to become available"""
        print("🔍 Debugging MLflow deployment...")

        # List MLflow deployment pods
        try:
            print("📋 MLflow deployment pods:")
            pods = self.run_command(
                f"kubectl get pods -l app=mlflow -n {self.args.namespace}",
                check=False, capture_output=True
            )
            if pods:
                print(pods)
            else:
                print("No MLflow pods found")

            # Get pod names for logs
            pod_names = self.run_command(
                f"kubectl get pods -l app=mlflow -n {self.args.namespace} "
                f"-o jsonpath='{{.items[*].metadata.name}}'",
                check=False, capture_output=True
            )

            if pod_names:
                for pod_name in pod_names.split():
                    print(f"📋 Pod logs for {pod_name}:")
                    logs = self.run_command(
                        f"kubectl logs {pod_name} -n {self.args.namespace} --tail=100",
                        check=False, capture_output=True
                    )
                    if logs:
                        print(logs)
                    else:
                        print("No logs available")

                    # Also get pod description
                    print(f"📋 Pod description for {pod_name}:")
                    description = self.run_command(
                        f"kubectl describe pod {pod_name} -n {self.args.namespace}",
                        check=False, capture_output=True
                    )
                    if description:
                        print(description)

        except Exception as e:
            print(f"❌ Failed to get MLflow deployment debug info: {e}")

        # Check deployment status
        try:
            print("📋 MLflow deployment status:")
            deployment_status = self.run_command(
                f"kubectl describe deployment mlflow -n {self.args.namespace}",
                check=False, capture_output=True
            )
            if deployment_status:
                print(deployment_status)
        except Exception as e:
            print(f"❌ Failed to get deployment status: {e}")

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
            self.create_mlflow_cr()

            # Step 5: Setup port forwarding info
            self.setup_port_forward()

            print("✅ MLflow deployment completed successfully!")

        except Exception as e:
            print(f"❌ Deployment failed: {e}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Deploy MLflow on Kind cluster")

    # Basic configuration
    parser.add_argument("--namespace", default="mlflow",
                       help="Kubernetes namespace")
    parser.add_argument("--target-branch", default="master",
                       help="Target branch for MLflow image")

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