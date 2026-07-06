"""Kubernetes Namespace management."""

from kubernetes import client
from kubernetes.client.rest import ApiException


class K8Manager:
    """Manager for Kubernetes namespace operations."""

    def __init__(self, core_v1_api: client.CoreV1Api):
        """Initialize K8Manager.

        Args:
            core_v1_api: Kubernetes CoreV1 API client
        """
        self.core_v1_api = core_v1_api

    def create_namespace(self, name: str) -> None:
        """Create a Kubernetes namespace.

        Args:
            name: Namespace name

        Raises:
            ApiException: If creation fails (except already exists)
        """
        namespace = client.V1Namespace(
            metadata=client.V1ObjectMeta(name=name)
        )

        try:
            self.core_v1_api.create_namespace(body=namespace)
        except ApiException as e:
            if e.status != 409:  # Ignore if already exists
                raise

    def label_namespace(self, name: str, labels: dict[str, str]) -> None:
        """Apply labels to a Kubernetes namespace.

        Args:
            name: Namespace name
            labels: Labels to merge into the namespace metadata

        Raises:
            ApiException: If patching fails
        """
        patch = {"metadata": {"labels": labels}}
        self.core_v1_api.patch_namespace(name=name, body=patch)

    def delete_namespace(self, name: str) -> None:
        """Delete a Kubernetes namespace.

        Args:
            name: Namespace name

        Raises:
            ApiException: If deletion fails (except not found)
        """
        try:
            self.core_v1_api.delete_namespace(name=name)
        except ApiException as e:
            if e.status != 404:
                raise
