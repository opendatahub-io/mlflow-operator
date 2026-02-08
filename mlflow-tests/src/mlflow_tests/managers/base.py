"""Abstract base class for user management."""

from abc import ABC, abstractmethod
from typing import Any

from mlflow_tests.enums import ResourceType, UserRole


class UserManager(ABC):
    """Abstract base class for managing users and permissions.

    Defines the interface for creating users, roles, and workspace configurations
    across different backends (K8s, MLflow).
    """

    @abstractmethod
    def create_user(self, username: str, other_info: str) -> tuple[str, str]:
        """Create a user in the system.

        Args:
            username: Username identifier
            other_info: Namespace in K8s mode and password in non K8s mode

        Returns:
            User details including authentication token
        """
        pass

    @abstractmethod
    def create_role(
        self,
        name: str,
        workspace_name: str,
        role: UserRole,
        resources: list[ResourceType],
    ) -> None:
        """Create a role with specific permissions.

        Args:
            name: User name or service account name
            workspace_name: Workspace Name where role applies
            role: Permission level
            resources: Resources the role can access
        """
        pass

    @abstractmethod
    def delete_user(self, username: str, namespace: str = None) -> None:
        """Delete a user from the system.

        Args:
            username: Username or ServiceAccount name to delete
            namespace: Namespace for K8s resources (optional, only used for K8s)

        Raises:
            Exception: If user deletion fails
        """
        pass