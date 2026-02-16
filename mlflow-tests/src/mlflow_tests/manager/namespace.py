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
