#!/usr/bin/env python3
"""
Role and RoleBinding Creation Module

This module handles the creation of Kubernetes roles, rolebindings, and service accounts
for MLflow users. Extracted from user_creation.py to provide modular role management.
"""

import subprocess
import sys
import yaml
from pathlib import Path


class RoleManager:
    def __init__(self):
        # Define permission mappings
        self.permission_types = {
            "cluster-admin": {
                "description": "Full cluster access",
                "cluster_role": "cluster-admin",
                "binding_type": "ClusterRoleBinding",
                "mlflow_access": True,
                "mlflow_role": "mlflow-edit"
            },
            "admin": {
                "description": "Namespace admin (edit + delete)",
                "cluster_role": "admin",
                "binding_type": "RoleBinding",
                "mlflow_access": True,
                "mlflow_role": "mlflow-edit"
            },
            "edit": {
                "description": "Namespace edit access",
                "cluster_role": "edit",
                "binding_type": "RoleBinding",
                "mlflow_access": True,
                "mlflow_role": "mlflow-edit"
            },
            "view": {
                "description": "Namespace readonly access with MLflow",
                "cluster_role": "view",
                "binding_type": "RoleBinding",
                "mlflow_access": True,
                "mlflow_role": "mlflow-view"
            },
            "view-no-mlflow": {
                "description": "Namespace readonly access without MLflow APIs",
                "cluster_role": None,  # Custom role
                "binding_type": "RoleBinding",
                "mlflow_access": False,
                "mlflow_role": None
            },
            "mlflow-only": {
                "description": "MLflow APIs only (no standard k8s resources)",
                "cluster_role": None,  # Custom role
                "binding_type": "RoleBinding",
                "mlflow_access": True,
                "mlflow_role": "mlflow-edit"
            }
        }

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

    def create_custom_readonly_role(self, namespace):
        """Create custom readonly role that excludes MLflow APIs"""
        role_name = f"readonly-no-mlflow-{namespace}"

        role = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {
                "name": role_name,
                "namespace": namespace
            },
            "rules": [
                # Standard Kubernetes resources (readonly)
                {
                    "apiGroups": [""],
                    "resources": [
                        "pods", "pods/log", "pods/status",
                        "services", "endpoints",
                        "persistentvolumeclaims",
                        "configmaps"
                    ],
                    "verbs": ["get", "list", "watch"]
                },
                {
                    "apiGroups": ["apps"],
                    "resources": [
                        "deployments", "replicasets", "statefulsets",
                        "daemonsets"
                    ],
                    "verbs": ["get", "list", "watch"]
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["networkpolicies", "ingresses"],
                    "verbs": ["get", "list", "watch"]
                },
                {
                    "apiGroups": ["batch"],
                    "resources": ["jobs", "cronjobs"],
                    "verbs": ["get", "list", "watch"]
                }
                # Explicitly EXCLUDE mlflow.opendatahub.io and mlflow.kubeflow.org
            ]
        }

        return role, role_name

    def create_custom_mlflow_only_role(self, namespace):
        """Create custom role that only allows MLflow APIs"""
        role_name = f"mlflow-only-{namespace}"

        role = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {
                "name": role_name,
                "namespace": namespace
            },
            "rules": [
                # MLflow APIs only
                {
                    "apiGroups": ["mlflow.opendatahub.io"],
                    "resources": ["mlflows", "mlflows/status"],
                    "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]
                },
                {
                    "apiGroups": ["mlflow.kubeflow.org"],
                    "resources": ["experiments", "registeredmodels", "jobs"],
                    "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]
                }
            ]
        }

        return role, role_name

    def create_service_account(self, username, namespace, permission_type):
        """Create ServiceAccount for a user"""
        service_account = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": username,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/created-by": "mlflow-user-manager",
                    "mlflow-permission-type": permission_type
                }
            }
        }
        return service_account

    def create_role_binding(self, username, namespace, permission_type):
        """Create appropriate role binding for a user based on permission type"""
        if permission_type not in self.permission_types:
            raise ValueError(f"Unknown permission type: {permission_type}")

        perm_config = self.permission_types[permission_type]
        resources = []

        if permission_type == "cluster-admin":
            # Cluster-wide access
            cluster_role_binding = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {
                    "name": f"{username}-cluster-admin",
                    "labels": {
                        "app.kubernetes.io/created-by": "mlflow-user-manager"
                    }
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "cluster-admin"
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": username,
                    "namespace": namespace
                }]
            }
            resources.append(cluster_role_binding)

        elif permission_type == "view-no-mlflow":
            # Custom readonly role without MLflow APIs
            custom_role, role_name = self.create_custom_readonly_role(namespace)
            resources.append(custom_role)

            role_binding = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": f"{username}-readonly-no-mlflow",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/created-by": "mlflow-user-manager"
                    }
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": role_name
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": username,
                    "namespace": namespace
                }]
            }
            resources.append(role_binding)

        elif permission_type == "mlflow-only":
            # Custom role for MLflow APIs only
            custom_role, role_name = self.create_custom_mlflow_only_role(namespace)
            resources.append(custom_role)

            role_binding = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": f"{username}-mlflow-only",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/created-by": "mlflow-user-manager"
                    }
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": role_name
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": username,
                    "namespace": namespace
                }]
            }
            resources.append(role_binding)

        else:
            # Standard cluster role binding
            role_binding = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": perm_config["binding_type"],
                "metadata": {
                    "name": f"{username}-{permission_type}",
                    "namespace": namespace if perm_config["binding_type"] == "RoleBinding" else None,
                    "labels": {
                        "app.kubernetes.io/created-by": "mlflow-user-manager"
                    }
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": perm_config["cluster_role"]
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": username,
                    "namespace": namespace
                }]
            }
            # Remove namespace from ClusterRoleBinding metadata
            if perm_config["binding_type"] == "ClusterRoleBinding":
                del role_binding["metadata"]["namespace"]

            resources.append(role_binding)

        # Add MLflow API access if enabled
        if perm_config["mlflow_access"] and perm_config["mlflow_role"]:
            mlflow_role_binding = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": f"{username}-mlflow-apis",
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/created-by": "mlflow-user-manager"
                    }
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": perm_config["mlflow_role"]
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": username,
                    "namespace": namespace
                }]
            }
            resources.append(mlflow_role_binding)

        return resources

    def create_user_rbac(self, username, namespace, permission_type):
        """Create complete RBAC resources for a single user"""
        print(f"👤 Creating RBAC for user '{username}' in namespace '{namespace}' with '{permission_type}' permissions...")

        if permission_type not in self.permission_types:
            print(f"❌ Unknown permission type: {permission_type}")
            print(f"   Available types: {list(self.permission_types.keys())}")
            return

        perm_config = self.permission_types[permission_type]
        resources = []

        # 1. Create ServiceAccount
        service_account = self.create_service_account(username, namespace, permission_type)
        resources.append(service_account)

        # 2. Create Role/RoleBinding resources
        role_resources = self.create_role_binding(username, namespace, permission_type)
        resources.extend(role_resources)

        # Apply all resources
        description = f"Creating RBAC for {username} ({perm_config['description']})"
        self.apply_yaml(resources, description)

    def get_permission_types(self):
        """Return available permission types and their descriptions"""
        return self.permission_types