"""Kubernetes user management implementation."""

import logging
from typing import Any, Optional

from kubernetes import client

from mlflow_tests.enums import ResourceType, KubeVerb
from mlflow_tests.manager.rbac import K8RoleManager
from mlflow_tests.manager.service_account import ServiceAccountManager

logger = logging.getLogger(__name__)


class K8UserManager:
    """Kubernetes user manager.

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

    def create_user(self, username: str, namespace: str) -> tuple[str, str]:
        """Create a user as a Kubernetes ServiceAccount.

        Args:
            username: ServiceAccount name
            namespace: Namespace for the ServiceAccount

        Returns:
            User details including token for authentication
        """
        logger.info(f"Creating K8s user (ServiceAccount) '{username}' in namespace '{namespace}'")
        try:
            result = self.sa_manager.create_sa_and_get_token(username, namespace)
            logger.info(f"Successfully created K8s user '{username}'")
            return result
        except Exception as e:
            logger.error(f"Failed to create K8s user '{username}' in namespace '{namespace}': {e}")
            raise

    def create_role(
        self,
        name: str,
        workspace_name: str,
        verbs: list[KubeVerb],
        resources: list[ResourceType],
        subresources: list[str] = None,
    ) -> None:
        """Create a Kubernetes Role and bind it to a user.

        Args:
            name: User/ServiceAccount name (used for role and binding)
            workspace_name: Namespace for role and binding
            verbs: List of Kubernetes verbs to grant
            resources: Resources to grant access to
            subresources: Optional list of subresources (e.g., ["gatewaysecrets/use"])
        """
        role_name = f"{name}-role"
        binding_name = f"{name}-binding"

        logger.info(f"Creating K8s role '{role_name}' for user '{name}' in namespace '{workspace_name}'")
        logger.debug(f"Role details - Verbs: {[v.value for v in verbs]}, Resources: {[r.value for r in resources]}")

        # Get the verb strings and resources for logging
        verb_strings = [verb.value for verb in verbs]
        k8s_resources = [r.get_k8s_resource() for r in resources]
        logger.info(f"RBAC Permissions - Verbs: {verb_strings}, MLflow Resources: {k8s_resources}")
        if subresources:
            logger.info(f"Subresources: {subresources}")
        logger.info(f"Additional permissions: Core K8s API (namespaces, serviceaccounts, secrets), RBAC read access")

        # Create the role
        self.role_manager.create_role(
            role_name, workspace_name, verbs, resources, subresources
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
            # Test the most important verb available (delete > create > update > get > list)
            available_verbs = [v.value for v in verbs]
            if "delete" in available_verbs:
                test_verb = "delete"
            elif "create" in available_verbs:
                test_verb = "create"
            elif "update" in available_verbs:
                test_verb = "update"
            elif "get" in available_verbs:
                test_verb = "get"
            elif "list" in available_verbs:
                test_verb = "list"
            else:
                # Fallback to first available verb if none of the above match
                test_verb = available_verbs[0] if available_verbs else "get"
                logger.warning(f"Using fallback verb '{test_verb}' for RBAC verification")

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

        logger.info(f"User '{name}' now has {verb_strings} access to {len(resources)} resource types in namespace '{workspace_name}'")

    def delete_user(self, username: str, namespace: Optional[str] = None) -> None:
        """Delete a Kubernetes ServiceAccount and associated RBAC resources.

        Args:
            username: ServiceAccount name to delete
            namespace: Namespace of the ServiceAccount

        Raises:
            ValueError: If namespace is not provided

        Note:
            This method deletes the ServiceAccount, Role, and RoleBinding.
            Individual deletion failures are logged but don't halt the overall process.
        """
        if not namespace:
            logger.error(f"Cannot delete service account '{username}': namespace is required")
            raise ValueError("Namespace is required to delete a Kubernetes ServiceAccount")

        logger.info(f"Deleting user '{username}' and associated RBAC resources in namespace '{namespace}'")

        # Delete ServiceAccount
        self.sa_manager.delete_service_account(username, namespace)

        # Delete Role
        role_name = f"{username}-role"
        try:
            logger.info(f"Deleting role '{role_name}' in namespace '{namespace}'")
            self.role_manager.rbac_v1_api.delete_namespaced_role(
                name=role_name,
                namespace=namespace
            )
            logger.info(f"Successfully deleted role '{role_name}'")
        except Exception as e:
            logger.warning(f"Failed to delete role '{role_name}': {e}")

        # Delete RoleBinding
        binding_name = f"{username}-binding"
        try:
            logger.info(f"Deleting role binding '{binding_name}' in namespace '{namespace}'")
            self.role_manager.rbac_v1_api.delete_namespaced_role_binding(
                name=binding_name,
                namespace=namespace
            )
            logger.info(f"Successfully deleted role binding '{binding_name}'")
        except Exception as e:
            logger.warning(f"Failed to delete role binding '{binding_name}': {e}")

        logger.info(f"Completed deletion of user '{username}' and associated resources")