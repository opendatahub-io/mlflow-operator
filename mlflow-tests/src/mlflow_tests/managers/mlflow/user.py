"""MLflow user management implementation."""

import logging
from mlflow.server.auth.client import AuthServiceClient
from mlflow.server.auth.entities import User

from mlflow_tests.enums import ResourceType, UserRole
from mlflow_tests.managers.base import UserManager

logger = logging.getLogger(__name__)


class MlFlowUserManager(UserManager):
    """MLflow implementation of UserManager.

    Uses MLflow REST APIs for user and permission management.
    """

    def __init__(self, client: AuthServiceClient):
        """Initialize MlFlowUserManager.

        Args:
            client: MLflow client with admin permissions
        """
        self.client = client

    def create_user(self, username: str, other_info: str) -> tuple[str, str]:
        """Create a user in MLflow using REST API.

        Args:
            username: Username
            other_info: Password

        Returns:
            Tuple of (username, password)
        """
        logger.info(f"Creating MLflow user: {username}")
        try:
            user = self.client.create_user(username=username, password=other_info)
            logger.info(f"Successfully created MLflow user: {username}")
            return user.username, other_info
        except Exception as e:
            logger.error(f"Failed to create MLflow user '{username}': {e}")
            raise

    def create_role(
        self,
        name: str,
        resource_id_name: str,
        role: UserRole,
        resources: list[ResourceType],
    ) -> None:
        """Create permissions for a user in MLflow.

        Args:
            name: Username to grant permissions to
            resource_id_name: Resource identifier (experiment ID, model name, or run ID)
            role: Permission level (READ, EDIT, DELETE, MANAGE)
            resources: Resource types to grant access to

        Raises:
            ValueError: If resources is empty, contains unsupported types, or inputs are invalid
            Exception: If permission creation fails

        Note:
            - MLflow permissions are resource-specific (per experiment/model/run)
            - resource_id_name is interpreted based on resource type:
              * For EXPERIMENTS: resource_id_name = experiment_id
              * For REGISTERED_MODELS: resource_id_name = model_name
              * For JOBS (runs): resource_id_name = run_id
            - DELETE role maps to EDIT in MLflow (no separate DELETE permission)

        Examples:
            >>> # Grant READ permissions on an experiment
            >>> manager.create_role(
            ...     name="data-scientist",
            ...     resource_id_name="12345",  # experiment_id
            ...     role=UserRole.READ,
            ...     resources=[ResourceType.EXPERIMENTS]
            ... )

            >>> # Grant MANAGE permissions on a registered model
            >>> manager.create_role(
            ...     name="ml-engineer",
            ...     resource_id_name="production-model",  # model name
            ...     role=UserRole.MANAGE,
            ...     resources=[ResourceType.REGISTERED_MODELS]
            ... )
        """
        # Input validation
        if not name or not name.strip():
            logger.error("Attempted to create role with empty username")
            raise ValueError("Username cannot be empty")

        if not resource_id_name or not resource_id_name.strip():
            logger.error(f"Attempted to create role for user '{name}' with empty resource identifier")
            raise ValueError("Resource identifier cannot be empty")

        if not resources:
            logger.error(f"Attempted to create role for user '{name}' with empty resources list")
            raise ValueError("Resources list cannot be empty")

        logger.info(f"Creating MLflow permissions for user '{name}' on {len(resources)} resource type(s)")

        # MLflow permission mapping
        permission_map = {
            UserRole.READ: "READ",
            UserRole.EDIT: "EDIT",
            UserRole.DELETE: "EDIT",  # MLflow doesn't have separate DELETE
            UserRole.MANAGE: "MANAGE",
        }

        # Validate role is in mapping
        if role not in permission_map:
            logger.error(f"Unsupported role type: {role}")
            raise ValueError(f"Unsupported role: {role}")

        mlflow_permission = permission_map[role]
        logger.debug(f"Mapped role {role.value} to MLflow permission: {mlflow_permission}")

        # Process each resource type
        for resource in resources:
            try:
                logger.debug(f"Creating {mlflow_permission} permission for user '{name}' on {resource.value} '{resource_id_name}'")

                if resource == ResourceType.EXPERIMENTS:
                    # Create experiment permission
                    # resource_id_name is interpreted as experiment_id
                    self.client.create_experiment_permission(
                        experiment_id=resource_id_name,
                        username=name,
                        permission=mlflow_permission,
                    )
                    logger.info(f"Created {mlflow_permission} permission for user '{name}' on experiment '{resource_id_name}'")
                elif resource == ResourceType.REGISTERED_MODELS:
                    # Create registered model permission
                    # resource_id_name is interpreted as model name
                    self.client.create_registered_model_permission(
                        name=resource_id_name,
                        username=name,
                        permission=mlflow_permission,
                    )
                    logger.info(f"Created {mlflow_permission} permission for user '{name}' on model '{resource_id_name}'")
                else:
                    logger.error(f"Unsupported resource type: {resource}")
                    raise ValueError(f"Unsupported resource type: {resource}")
            except ValueError:
                # Re-raise ValueError as-is (validation errors)
                raise
            except Exception as e:
                # Wrap other exceptions with more context
                error_msg = f"Failed to create {mlflow_permission} permission for user '{name}' on {resource.value} '{resource_id_name}': {str(e)}"
                logger.error(error_msg)
                raise type(e)(error_msg) from e

    def delete_user(self, username: str, namespace: str = None) -> None:
        """Delete a user from MLflow.

        Args:
            username: Username to delete
            namespace: Not used for MLflow (only for K8s compatibility)

        Raises:
            Exception: If user deletion fails

        Note:
            This method deletes the user and all associated permissions.
            The namespace parameter is ignored for MLflow users.
        """
        if not username or not username.strip():
            logger.error("Attempted to delete user with empty username")
            raise ValueError("Username cannot be empty")

        logger.info(f"Deleting MLflow user: {username}")
        try:
            self.client.delete_user(username=username.strip())
            logger.info(f"Successfully deleted MLflow user: {username}")
        except Exception as e:
            error_msg = f"Failed to delete user '{username}': {str(e)}"
            logger.error(error_msg)
            raise type(e)(error_msg) from e
