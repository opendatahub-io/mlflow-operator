"""Kubernetes user management implementation."""

import logging
from typing import Any

from kubernetes import client

from mlflow_tests.enums import ResourceType, UserRole
from mlflow_tests.managers.base import UserManager
from mlflow_tests.managers.k8s.rbac import K8RoleManager
from mlflow_tests.managers.k8s.service_account import ServiceAccountManager

logger = logging.getLogger(__name__)


class K8UserManager(UserManager):
    """Kubernetes implementation of UserManager.

    Manages users via ServiceAccounts and RBAC.
    """

    def __init__(
        self, core_v1_api: client.CoreV1Api, rbac_v1_api: client.RbacAuthorizationV1Api
    ):
        """Initialize K8UserManager.

        Args:
            core_v1_api: Kubernetes CoreV1 API client
            rbac_v1_api: Kubernetes RBAC API client
        """
        self.core_v1_api = core_v1_api
        self.role_manager = K8RoleManager(rbac_v1_api)
        self.sa_manager = ServiceAccountManager(core_v1_api)

    def create_user(self, username: str, other_info: str) -> tuple[str, str]:
        """Create a user as a Kubernetes ServiceAccount.

        Args:
            username: ServiceAccount name
            other_info: Namespace for the ServiceAccount

        Returns:
            User details including token for authentication
        """
        logger.info(f"Creating K8s user (ServiceAccount) '{username}' in namespace '{other_info}'")
        try:
            result = self.sa_manager.create_sa_and_get_token(username, other_info)
            logger.info(f"Successfully created K8s user '{username}'")
            return result
        except Exception as e:
            logger.error(f"Failed to create K8s user '{username}' in namespace '{other_info}': {e}")
            raise

    def create_role(
        self,
        name: str,
        workspace_name: str,
        role: UserRole,
        resources: list[ResourceType],
    ) -> None:
        """Create a Kubernetes Role and bind it to a user.

        Args:
            name: User/ServiceAccount name (used for role and binding)
            workspace_name: Namespace for role and binding
            role: Permission level
            resources: Resources to grant access to
        """
        role_name = f"{name}-role"
        binding_name = f"{name}-binding"

        logger.info(f"Creating K8s role '{role_name}' for user '{name}' in namespace '{workspace_name}'")
        logger.debug(f"Role details - Permission level: {role.value}, Resources: {[r.value for r in resources]}")

        # Get the verbs and resources for logging
        verbs = role.get_k8s_verbs()
        k8s_resources = [r.get_k8s_resource() for r in resources]
        logger.info(f"RBAC Permissions - Verbs: {verbs}, MLflow Resources: {k8s_resources}")
        logger.info(f"Additional permissions: Core K8s API (namespaces, serviceaccounts, secrets), RBAC read access")

        # Create the role
        self.role_manager.create_role(
            role_name, workspace_name, role, resources
        )
        logger.info(f"Created K8s role '{role_name}' with comprehensive permissions")

        # Create the role binding
        logger.debug(f"Creating role binding '{binding_name}' for user '{name}'")
        self.role_manager.create_role_binding(
            binding_name, workspace_name, role_name, name
        )
        logger.info(f"Successfully created and bound role '{role_name}' to user '{name}'")

        # Verify permissions are actually usable by performing SubjectAccessReview
        logger.debug(f"Verifying RBAC permissions for user '{name}' are ready")
        for resource in resources:
            # Test the most important verb for the role (delete for EDIT/MANAGE, get for READ)
            test_verb = "delete" if "delete" in role.get_k8s_verbs() else "get"
            try:
                self.role_manager.verify_rbac_permissions(
                    service_account_name=name,
                    namespace=workspace_name,
                    resource=resource.get_k8s_resource(),
                    verb=test_verb,
                    max_retries=10,
                    retry_delay=1.0
                )
                logger.info(f"RBAC verification passed for {name} - can {test_verb} {resource.get_k8s_resource()}")
            except Exception as e:
                logger.error(f"RBAC verification failed for user '{name}': {e}")
                raise

        logger.info(f"User '{name}' now has {role.value} access to {len(resources)} resource types in namespace '{workspace_name}'")

    def delete_user(self, username: str, namespace: str = None) -> None:
        """Delete a Kubernetes ServiceAccount and associated resources.

        Args:
            username: ServiceAccount name to delete
            namespace: Namespace of the ServiceAccount

        Raises:
            Exception: If deletion fails
            ValueError: If namespace is not provided

        Note:
            This method attempts to clean up the ServiceAccount and associated
            secrets/role bindings. Failures are logged but don't halt the deletion process.
        """
        if not namespace:
            logger.error(f"Cannot delete service account '{username}': namespace is required")
            raise ValueError("Namespace is required to delete a Kubernetes ServiceAccount")

        self.sa_manager.delete_service_account(username, namespace)