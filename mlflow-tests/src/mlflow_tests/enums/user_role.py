"""User role enumeration for permission management."""

from enum import Enum


class UserRole(Enum):
    """Defines user roles and their associated permissions.

    Maps to Kubernetes RBAC verbs for resource access control.
    Based on MLflow Kubeflow operator RBAC patterns.
    """

    READ = "read"
    EDIT = "edit"
    USE = "use"          # Fixed: USE should have "use" value, not "delete"
    MANAGE = "manage"

    def get_k8s_verbs(self) -> list[str]:
        """Get Kubernetes RBAC verbs for main resources.

        Returns:
            List of K8s verbs corresponding to the role for main resources
        """
        verb_mapping = {
            UserRole.READ: ["get", "list"],
            UserRole.EDIT: ["get", "list", "create", "update", "delete"],
            UserRole.USE: ["create"],  # Can use but not modify/delete
            UserRole.MANAGE: ["get", "list", "create", "update", "delete"],
        }
        return verb_mapping[self]

    def get_k8s_sub_resource_verbs(self) -> list[str]:
        """Get Kubernetes RBAC verbs for sub-resources (e.g., gatewaysecrets/use).

        Returns:
            List of K8s verbs for sub-resources. Sub-resources typically only support 'create'.
        """
        # Sub-resources like 'gatewaysecrets/use' typically only support 'create' verb
        # which represents "using" the resource
        sub_resource_verb_mapping = {
            UserRole.READ: [],  # Read-only users cannot use gateway resources
            UserRole.EDIT: ["create"],  # Can use gateway resources
            UserRole.USE: ["create"],   # Specifically designed for using resources
            UserRole.MANAGE: ["create"], # Can use gateway resources
        }
        return sub_resource_verb_mapping[self]

    def can_use_gateway_resources(self) -> bool:
        """Check if this role can use MLflow Gateway resources.

        Returns:
            True if role can create on gateway sub-resources (e.g., gatewaysecrets/use)
        """
        return len(self.get_k8s_sub_resource_verbs()) > 0

    def can_delete_resources(self) -> bool:
        """Check if this role can delete main resources.

        Returns:
            True if role includes 'delete' verb for main resources
        """
        return "delete" in self.get_k8s_verbs()

    def can_modify_resources(self) -> bool:
        """Check if this role can create or update main resources.

        Returns:
            True if role includes 'create' or 'update' verb for main resources
        """
        verbs = self.get_k8s_verbs()
        return "create" in verbs or "update" in verbs

    @classmethod
    def get_role_for_testing_delete(cls) -> 'UserRole':
        """Get the minimum role required for testing delete operations.

        Returns:
            UserRole that can perform delete operations
        """
        return cls.EDIT

    @classmethod
    def get_role_for_testing_gateway_use(cls) -> 'UserRole':
        """Get the minimum role required for testing gateway resource usage.

        Returns:
            UserRole that can use gateway resources
        """
        return cls.USE
