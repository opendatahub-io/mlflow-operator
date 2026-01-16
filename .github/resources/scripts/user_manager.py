#!/usr/bin/env python3
"""
MLflow User Manager

This script creates actual Kubernetes users with certificates and configures RBAC.
Integrates certificate creation and role management for complete user setup.

Example usage:
    # Using default configuration
    python user_manager.py

    # Using custom JSON configuration
    python user_manager.py --config sample-config.json

    # Using inline configuration
    python user_manager.py --workspace-users '{"mlflow":{"admin-user":"cluster-admin","editor":"edit","viewer":"view"},"workspace-b":{"workspace-admin":"edit","guest":"view-no-mlflow"}}'
"""

import subprocess
import sys
import yaml
import json
from pathlib import Path
import argparse
from role_manager import RoleManager
from cert_manager import CertManager


class UserManager:
    def __init__(self, workspace_users_config=None, default_mlflow_namespace="mlflow", create_certificates=True, cluster_name="kind"):
        self.default_mlflow_namespace = default_mlflow_namespace
        self.create_certificates = create_certificates
        self.cluster_name = cluster_name

        # Default configuration if none provided
        if workspace_users_config is None:
            workspace_users_config = {
                self.default_mlflow_namespace: {
                    "user1": "cluster-admin",
                    "user2": "edit",
                    "user3": "view"
                },
                "workspace-b": {
                    "user4": "edit",
                    "user5": "view-no-mlflow"
                }
            }

        self.workspace_users = workspace_users_config

        # Initialize managers with shared run_command function
        self.role_manager = RoleManager()
        self.cert_manager = CertManager(run_command_func=self.run_command, cluster_name=cluster_name) if create_certificates else None

        # Get permission types from role manager
        self.permission_types = self.role_manager.get_permission_types()

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
            if capture_output and e.stderr:
                print(f"STDERR: {e.stderr}")
            if check:
                sys.exit(1)
            return None

    def apply_yaml(self, yaml_content, description):
        """Apply YAML content to Kubernetes"""
        temp_file = Path("/tmp/temp_resource.yaml")

        with open(temp_file, 'w') as f:
            yaml.dump_all(yaml_content, f, default_flow_style=False)

        self.run_command(f"kubectl apply -f {temp_file}", description)
        temp_file.unlink()

    def create_namespace(self, namespace):
        """Create namespace if it doesn't exist"""
        print(f"🏗️  Ensuring namespace '{namespace}' exists...")

        # Try to create, ignore if it already exists
        self.run_command(
            f"kubectl create namespace {namespace}",
            f"Creating namespace {namespace}",
            check=False
        )

    def create_complete_user(self, username, namespace, permission_type):
        """Create a complete user with RBAC and optionally certificates"""
        print(f"🚀 Creating complete user setup for '{username}' in namespace '{namespace}'...")

        # Step 1: Create RBAC using RoleManager
        self.role_manager.create_user_rbac(username, namespace, permission_type)

        # Step 2: Create certificate-based user if enabled
        kubeconfig_file = None
        if self.create_certificates and self.cert_manager:
            print(f"🔐 Creating certificate for user '{username}'...")

            # Determine groups based on permission type
            groups = ["system:authenticated"]
            if permission_type == "cluster-admin":
                groups.append("system:masters")

            cert_result = self.cert_manager.create_user_certificate(
                username=username,
                namespace=namespace,
                groups=groups
            )

            if cert_result:
                kubeconfig_file = cert_result["kubeconfig"]
                print(f"✅ Certificate created for '{username}': {kubeconfig_file}")

                # Verify user access
                self.cert_manager.verify_user_access(kubeconfig_file, namespace)
            else:
                print(f"⚠️ Certificate creation failed for '{username}', but RBAC was created")

        return {
            "username": username,
            "namespace": namespace,
            "permission_type": permission_type,
            "kubeconfig_file": kubeconfig_file
        }

    def create_kubeconfig_instructions(self):
        """Generate kubeconfig instructions for all created users"""
        print("\n" + "="*80)
        print("📋 KUBECONFIG CREATION INSTRUCTIONS")
        print("="*80)

        for namespace, users in self.workspace_users.items():
            print(f"\n🏗️  WORKSPACE: {namespace.upper()}")
            print("-" * 50)

            for username, permission_type in users.items():
                perm_desc = self.permission_types[permission_type]["description"]
                print(f"\n🔑 {username.upper()} ({perm_desc}):")
                print(f"   # Get the token")
                print(f"   TOKEN=$(kubectl get secret $(kubectl get serviceaccount {username} -n {namespace} -o jsonpath='{{.secrets[0].name}}') -n {namespace} -o jsonpath='{{.data.token}}' | base64 -d)")
                print(f"   ")
                print(f"   # Create kubeconfig")
                print(f"   kubectl config set-cluster kind --server=https://localhost:6443 --certificate-authority=/path/to/ca.crt")
                print(f"   kubectl config set-credentials {username} --token=$TOKEN")
                print(f"   kubectl config set-context {username}-context --cluster=kind --user={username} --namespace={namespace}")
                print(f"   kubectl config use-context {username}-context")

    def generate_rbac_tests(self):
        """Generate RBAC verification commands"""
        print("\n" + "="*80)
        print("🔍 RBAC VERIFICATION COMMANDS")
        print("="*80)

        test_cases = []

        for namespace, users in self.workspace_users.items():
            for username, permission_type in users.items():
                perm_config = self.permission_types[permission_type]

                # Standard k8s resource tests
                if permission_type == "cluster-admin":
                    test_cases.append((username, namespace, "pods", "", "create", "Should succeed (cluster-admin)"))
                    test_cases.append((username, "default", "pods", "", "create", "Should succeed (cluster-admin)"))
                elif permission_type in ["admin", "edit"]:
                    test_cases.append((username, namespace, "pods", "", "create", f"Should succeed ({permission_type})"))
                    test_cases.append((username, "default", "pods", "", "create", "Should fail (namespace scoped)"))
                elif permission_type in ["view", "view-no-mlflow"]:
                    test_cases.append((username, namespace, "pods", "", "get", "Should succeed (readonly)"))
                    test_cases.append((username, namespace, "pods", "", "create", "Should fail (readonly)"))
                elif permission_type == "mlflow-only":
                    test_cases.append((username, namespace, "pods", "", "get", "Should fail (MLflow APIs only)"))

                # MLflow API tests
                if perm_config["mlflow_access"]:
                    if perm_config["mlflow_role"] == "mlflow-edit":
                        test_cases.append((username, namespace, "mlflows", "mlflow.opendatahub.io", "create", "Should succeed (MLflow edit)"))
                    elif perm_config["mlflow_role"] == "mlflow-view":
                        test_cases.append((username, namespace, "mlflows", "mlflow.opendatahub.io", "get", "Should succeed (MLflow view)"))
                        test_cases.append((username, namespace, "mlflows", "mlflow.opendatahub.io", "create", "Should fail (MLflow readonly)"))
                else:
                    test_cases.append((username, namespace, "mlflows", "mlflow.opendatahub.io", "get", "Should fail (no MLflow access)"))

        print("Run these commands to verify permissions:")
        print()

        for username, namespace, resource, api_group, verb, expected in test_cases:
            api_flag = f"--api-group={api_group}" if api_group else ""
            print(f"# {expected}")
            print(f"kubectl auth can-i {verb} {resource} {api_flag} --as=system:serviceaccount:{namespace}:{username} -n {namespace}")
            print()

    def print_configuration_summary(self):
        """Print summary of the configuration that will be created"""
        print("📊 CONFIGURATION SUMMARY")
        print("="*80)

        total_users = sum(len(users) for users in self.workspace_users.values())
        total_namespaces = len(self.workspace_users)

        print(f"Total Namespaces: {total_namespaces}")
        print(f"Total Users: {total_users}")
        print()

        for namespace, users in self.workspace_users.items():
            print(f"🏗️  Namespace: {namespace}")
            for username, permission_type in users.items():
                perm_desc = self.permission_types[permission_type]["description"]
                print(f"   👤 {username}: {permission_type} ({perm_desc})")
            print()

    def create_all_users(self):
        """Main method to create all users and workspaces"""
        cert_mode = "with certificates" if self.create_certificates else "RBAC only"
        print(f"🚀 Creating MLflow users and workspaces {cert_mode}...")
        print()

        try:
            # Print configuration summary
            self.print_configuration_summary()

            # Setup CA files if using certificates
            if self.create_certificates and self.cert_manager:
                print("🔧 Setting up certificate infrastructure...")
                # Try to setup CA files from cluster if needed
                if not Path(self.cert_manager.ca_cert_path).exists():
                    self.cert_manager.setup_ca_for_cluster()

            # Create all namespaces
            for namespace in self.workspace_users.keys():
                self.create_namespace(namespace)

            # Track created users for summary
            created_users = []

            # Create complete users (RBAC + certificates)
            for namespace, users in self.workspace_users.items():
                print(f"\n🏗️  Processing namespace '{namespace}'...")
                for username, permission_type in users.items():
                    user_result = self.create_complete_user(username, namespace, permission_type)
                    created_users.append(user_result)

            print("\n✅ All users and workspaces created successfully!")

            # Print summary of created users
            self.print_user_summary(created_users)

            # Generate instructions and tests
            if not self.create_certificates:
                self.create_kubeconfig_instructions()
            self.generate_rbac_tests()

        except Exception as e:
            print(f"❌ User creation failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    def print_user_summary(self, created_users):
        """Print summary of created users with their kubeconfig files"""
        print("\n" + "="*80)
        print("📋 CREATED USERS SUMMARY")
        print("="*80)

        for user in created_users:
            username = user["username"]
            namespace = user["namespace"]
            permission_type = user["permission_type"]
            kubeconfig_file = user["kubeconfig_file"]

            perm_desc = self.permission_types[permission_type]["description"]
            print(f"\n👤 {username} ({namespace})")
            print(f"   Permission: {permission_type} ({perm_desc})")

            if kubeconfig_file:
                print(f"   Kubeconfig: {kubeconfig_file}")
                print(f"   Usage: kubectl --kubeconfig={kubeconfig_file} get pods")
            else:
                print(f"   Authentication: ServiceAccount token (see instructions below)")

        if any(user["kubeconfig_file"] for user in created_users):
            print(f"\n📖 All certificate-based kubeconfig files are in: /tmp/k8s-user-certs/")


def main():
    parser = argparse.ArgumentParser(
        description="Create MLflow users with certificates and RBAC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Permission Types:
  cluster-admin     - Full cluster access
  admin             - Namespace admin (edit + delete)
  edit              - Namespace edit access
  view              - Namespace readonly with MLflow APIs
  view-no-mlflow    - Namespace readonly without MLflow APIs
  mlflow-only       - Only MLflow APIs (no standard k8s resources)

Example configurations:
  Default: Creates user1-user5 with mixed permissions across mlflow and workspace-b

  Custom JSON:
  {
    "production": {
      "data-scientist": "view",
      "ml-engineer": "edit"
    },
    "staging": {
      "developer": "admin",
      "tester": "view-no-mlflow"
    }
  }

Authentication Options:
  By default, creates both ServiceAccounts (RBAC) AND certificate-based users.
  Use --no-certificates to create only ServiceAccounts with tokens.
        """
    )

    parser.add_argument("--config", type=str,
                       help="JSON configuration file path")
    parser.add_argument("--workspace-users", type=str,
                       help="Inline JSON configuration string")
    parser.add_argument("--mlflow-namespace", default="mlflow",
                       help="Default MLflow namespace (default: mlflow)")
    parser.add_argument("--cluster-name", default="kind",
                       help="Kubernetes cluster name for CA extraction (default: kind)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Print configuration without creating resources")
    parser.add_argument("--no-certificates", action="store_true",
                       help="Create only RBAC (ServiceAccounts), skip certificate creation")

    args = parser.parse_args()

    # Determine configuration source
    workspace_users_config = None

    if args.config:
        # Load from file
        with open(args.config, 'r') as f:
            workspace_users_config = json.load(f)
    elif args.workspace_users:
        # Load from inline JSON
        workspace_users_config = json.loads(args.workspace_users)
    # else: use default configuration

    user_manager = UserManager(
        workspace_users_config=workspace_users_config,
        default_mlflow_namespace=args.mlflow_namespace,
        create_certificates=not args.no_certificates,
        cluster_name=args.cluster_name
    )

    if args.dry_run:
        print("🔍 DRY RUN MODE - Configuration Summary:")
        user_manager.print_configuration_summary()
    else:
        user_manager.create_all_users()


if __name__ == "__main__":
    main()