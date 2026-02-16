from typing import Optional

from mlflow_tests.enums import ResourceType, KubeVerb
from mlflow.client import MlflowClient


class UserInfo:
    """Class for storing user information with getters and setters."""

    def __init__(self, uname: Optional[str] = None, upass: Optional[str] = None, workspace: Optional[str] = None, resource_types: Optional[list[ResourceType]] = None, verbs: Optional[list[KubeVerb]] = None, subresources: Optional[list[str]] = None, client: Optional[MlflowClient] = None):
        """Initialize UserInfo with username, password, workspace, resource type, verbs, and authenticated client.

        Args:
            uname: Username
            upass: User password
            workspace: User workspace
            resource_types: List of Resource type for the user
            verbs: List of Kubernetes verbs for permissions
            subresources: Optional list of subresources (e.g., ["gatewaysecrets/use"])
            client: Authenticated MLflow client for this user
        """
        self._uname = uname
        self._upass = upass
        self._workspace = workspace
        self._resource_types = resource_types
        self._verbs = verbs or []
        self._subresources = subresources or []
        self._client = client

    @property
    def uname(self) -> str:
        """Get the username.

        Returns:
            str: The username
        """
        return self._uname

    def set_uname(self, value: str) -> "UserInfo":
        """Set the username with method chaining support.

        Args:
            value: New username value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is empty or not a string
        """
        if not isinstance(value, str):
            raise ValueError("Username must be a string")
        if not value.strip():
            raise ValueError("Username cannot be empty")
        self._uname = value.strip()
        return self

    @property
    def upass(self) -> str:
        """Get the user password.

        Returns:
            str: The user password
        """
        return self._upass

    def set_upass(self, value: str) -> "UserInfo":
        """Set the user password with method chaining support.

        Args:
            value: New password value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is empty or not a string
        """
        if not isinstance(value, str):
            raise ValueError("Password must be a string")
        if not value:
            raise ValueError("Password cannot be empty")
        self._upass = value
        return self

    @property
    def workspace(self) -> str:
        """Get the workspace.

        Returns:
            str: The workspace
        """
        return self._workspace

    def set_workspace(self, value: str) -> "UserInfo":
        """Set the workspace with method chaining support.

        Args:
            value: New workspace value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is empty or not a string
        """
        if not isinstance(value, str):
            raise ValueError("Workspace must be a string")
        if not value.strip():
            raise ValueError("Workspace cannot be empty")
        self._workspace = value.strip()
        return self

    @property
    def resource_types(self) -> list[ResourceType]:
        """Get the resource types.

        Returns:
            list[ResourceType]: The list of resource types
        """
        return self._resource_types

    def set_resource_types(self, value: list[ResourceType]) -> "UserInfo":
        """Set the resource type with method chaining support.

        Args:
            value: New resource type value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is not a ResourceType
        """
        self._resource_types = value
        return self

    @property
    def verbs(self) -> list[KubeVerb]:
        """Get the user verbs.

        Returns:
            list[KubeVerb]: The user verbs
        """
        return self._verbs

    def set_verbs(self, value: list[KubeVerb]) -> "UserInfo":
        """Set the user verbs with method chaining support.

        Args:
            value: New verbs list

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is not a list of KubeVerb
        """
        if not isinstance(value, list):
            raise ValueError("Verbs must be a list")
        if value and not all(isinstance(verb, KubeVerb) for verb in value):
            raise ValueError("All verbs must be KubeVerb enum values")
        self._verbs = value
        return self

    @property
    def subresources(self) -> list[str]:
        """Get the user subresources.

        Returns:
            list[str]: The user subresources
        """
        return self._subresources

    def set_subresources(self, value: list[str]) -> "UserInfo":
        """Set the user subresources with method chaining support.

        Args:
            value: New subresources list

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is not a list of strings
        """
        if not isinstance(value, list):
            raise ValueError("Subresources must be a list")
        if value and not all(isinstance(subres, str) for subres in value):
            raise ValueError("All subresources must be strings")
        self._subresources = value
        return self

    @property
    def client(self) -> Optional[MlflowClient]:
        """Get the authenticated MLflow client.

        Returns:
            Optional[MlflowClient]: The authenticated MLflow client or None
        """
        return self._client

    def set_client(self, value: Optional[MlflowClient]) -> "UserInfo":
        """Set the authenticated MLflow client with method chaining support.

        Args:
            value: MLflow client instance or None

        Returns:
            UserInfo: The current instance for method chaining
        """
        self._client = value
        return self

    def __str__(self) -> str:
        """String representation of UserInfo.

        Returns:
            str: String representation (password is masked)
        """
        parts = []
        if self._uname is not None:
            parts.append(f"uname='{self._uname}'")
        if self._upass is not None:
            parts.append("upass='***'")
        if self._workspace is not None:
            parts.append(f"workspace='{self._workspace}'")
        if self._resource_types is not None:
            parts.append(f"resource_type={self._resource_types}")
        if self._verbs:
            verb_names = [verb.value for verb in self._verbs]
            parts.append(f"verbs={verb_names}")
        if self._subresources:
            parts.append(f"subresources={self._subresources}")

        return f"UserInfo({', '.join(parts)})"

    def __repr__(self) -> str:
        """Detailed representation of UserInfo.

        Returns:
            str: Detailed representation (password is masked)
        """
        return self.__str__()

    def __eq__(self, other) -> bool:
        """Check equality with another UserInfo instance.

        Args:
            other: Another UserInfo instance

        Returns:
            bool: True if all fields match
        """
        if not isinstance(other, UserInfo):
            return False
        return (self._uname == other._uname and
                self._upass == other._upass and
                self._workspace == other._workspace and
                self._resource_types == other._resource_types and
                self._verbs == other._verbs and
                self._subresources == other._subresources and
                self._client == other._client)

