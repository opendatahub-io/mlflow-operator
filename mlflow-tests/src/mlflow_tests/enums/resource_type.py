"""Resource type enumeration for MLflow resources."""

from enum import Enum


class ResourceType(Enum):
    """Defines MLflow resource types for RBAC.

    Maps to MLflow Kubernetes CRD resources and sub-resources.
    Based on mlflow.kubeflow.org API group resources.
    """

    # Core MLflow resources
    EXPERIMENTS = "experiments"
    REGISTERED_MODELS = "registeredmodels"
    RUNS = "jobs"

    # MLflow Gateway resources (for model serving and inference)
    GATEWAY_SECRETS = "gatewaysecrets"
    GATEWAY_ENDPOINTS = "gatewayendpoints"
    GATEWAY_MODEL_DEFINITIONS = "gatewaymodeldefinitions"

    def get_k8s_resource(self) -> str:
        """Get Kubernetes resource name.

        Returns:
            K8s resource name for RBAC rules
        """
        return self.value

    def get_k8s_sub_resources(self) -> list[str]:
        """Get Kubernetes sub-resource names for this resource.

        Returns:
            List of sub-resource names (e.g., 'gatewaysecrets/use')
            Empty list if no sub-resources exist for this resource type.
        """
        sub_resource_mapping = {
            ResourceType.GATEWAY_SECRETS: ["gatewaysecrets/use"],
            ResourceType.GATEWAY_ENDPOINTS: ["gatewayendpoints/use"],
            ResourceType.GATEWAY_MODEL_DEFINITIONS: ["gatewaymodeldefinitions/use"],
        }
        return sub_resource_mapping.get(self, [])

    def requires_use_permission(self) -> bool:
        """Check if this resource type has 'use' sub-resources requiring special permissions.

        Returns:
            True if this resource type has sub-resources that require 'create' verb on /use
        """
        return len(self.get_k8s_sub_resources()) > 0

    @classmethod
    def get_all_main_resources(cls) -> list[str]:
        """Get all main resource names for RBAC policy rules.

        Returns:
            List of all main resource names (excludes sub-resources)
        """
        return [resource.get_k8s_resource() for resource in cls]

    @classmethod
    def get_all_sub_resources(cls) -> list[str]:
        """Get all sub-resource names for RBAC policy rules.

        Returns:
            List of all sub-resource names (e.g., ['gatewaysecrets/use', ...])
        """
        sub_resources = []
        for resource in cls:
            sub_resources.extend(resource.get_k8s_sub_resources())
        return sub_resources
