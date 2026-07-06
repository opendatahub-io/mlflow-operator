"""Kubernetes RBAC verb enumeration for permission management."""

from enum import Enum


class KubeVerb(Enum):
    """Specific Kubernetes RBAC verbs for resource access control.

    Each verb represents a specific action that can be performed on K8s resources.
    Tests should use explicit verb combinations rather than predefined role mappings.
    """

    GET = "get"
    CREATE = "create"
    LIST = "list"
    UPDATE = "update"
    DELETE = "delete"

    def __str__(self) -> str:
        """Return the string value of the verb.

        Returns:
            String representation of the Kubernetes verb
        """
        return self.value
