from typing import Optional

from mlflow_tests.enums import ResourceType, UserRole


class UserInfo:
    """Class for storing user information with getters and setters."""

    def __init__(self, uname: Optional[str] = None, upass: Optional[str] = None, workspace: Optional[str] = None, resource_type: Optional[ResourceType] = None, role: Optional[UserRole] = None):
        """Initialize UserInfo with username, password, workspace, resource type, and role.

        Args:
            uname: Username
            upass: User password
            workspace: User workspace
            resource_type: Resource type for the user
            role: User role
        """
        self._uname = uname
        self._upass = upass
        self._workspace = workspace
        self._resource_type = resource_type
        self._role = role

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
    def resource_type(self) -> ResourceType:
        """Get the resource type.

        Returns:
            ResourceType: The resource type
        """
        return self._resource_type

    def set_resource_type(self, value: ResourceType) -> "UserInfo":
        """Set the resource type with method chaining support.

        Args:
            value: New resource type value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is not a ResourceType
        """
        if not isinstance(value, ResourceType):
            raise ValueError("Resource type must be a ResourceType enum")
        self._resource_type = value
        return self

    @property
    def role(self) -> UserRole:
        """Get the user role.

        Returns:
            UserRole: The user role
        """
        return self._role

    def set_role(self, value: UserRole) -> "UserInfo":
        """Set the user role with method chaining support.

        Args:
            value: New role value

        Returns:
            UserInfo: The current instance for method chaining

        Raises:
            ValueError: If value is not a UserRole
        """
        if not isinstance(value, UserRole):
            raise ValueError("Role must be a UserRole enum")
        self._role = value
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
        if self._resource_type is not None:
            parts.append(f"resource_type={self._resource_type}")
        if self._role is not None:
            parts.append(f"role={self._role}")

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
                self._resource_type == other._resource_type and
                self._role == other._role)

