"""Client creation utilities for Kubernetes and MLflow."""

import os
import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.client import Configuration
from mlflow.tracking import MlflowClient

logger = logging.getLogger(__name__)


class ClientManager:

    @classmethod
    def create_k8s_client(cls) -> tuple[client.CoreV1Api, client.RbacAuthorizationV1Api]:
        """Create Kubernetes API clients.

        Args:
            token: Optional service account token. If not provided, uses default kubeconfig.

        Returns:
            Tuple of (CoreV1Api, RbacAuthorizationV1Api) clients

        Raises:
            ValueError: If token is required but not provided
        """
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        core_v1_api = client.CoreV1Api()
        rbac_v1_api = client.RbacAuthorizationV1Api()

        return core_v1_api, rbac_v1_api


    @classmethod
    def create_mlflow_client(cls,
            username: str = None, password: str = None, token: str = None, tracking_uri: Optional[str] = None
    ) -> MlflowClient:
        """Create MLflow tracking client with proper authentication.

        CRITICAL: MLflow reads credentials from environment variables at client instantiation time.
        This means credentials MUST be set in environment BEFORE creating the client.

        Authentication modes:
        - LOCAL: Uses username/password (Basic Auth)
        - K8s: Uses token (Bearer token)

        Args:
            username: Username for MLflow Basic Auth (required for LOCAL mode)
            password: Password for MLflow Basic Auth (required for LOCAL mode)
            token: Bearer token for MLflow authentication (required for K8s mode)
            tracking_uri: MLflow tracking server URI

        Returns:
            Configured MLflow client with authentication credentials

        Raises:
            ValueError: If tracking URI is not provided or authentication credentials are missing

        Note:
            This function clears all MLflow auth environment variables first to ensure
            clean state, then sets only the appropriate credentials for the auth mode.
        """
        logger.debug(f"Creating MLflow client with tracking_uri={tracking_uri}")

        # Clear all MLflow authentication environment variables to ensure clean state
        # This prevents credential leakage between different authentication contexts
        auth_vars = ['MLFLOW_TRACKING_USERNAME', 'MLFLOW_TRACKING_PASSWORD', 'MLFLOW_TRACKING_TOKEN']
        for var in auth_vars:
            if var in os.environ:
                logger.debug(f"Clearing existing environment variable: {var}")
                del os.environ[var]

        # Set tracking URI first
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        logger.debug(f"Set MLflow tracking URI to: {tracking_uri}")

        # Set appropriate authentication credentials based on mode
        if username and password:
            # LOCAL mode: Basic authentication
            logger.debug(f"Setting up Basic Auth with username: {username}")
            os.environ['MLFLOW_TRACKING_USERNAME'] = username
            os.environ['MLFLOW_TRACKING_PASSWORD'] = password
            logger.info(f"MLflow client configured for Basic Auth (LOCAL mode) with user: {username}")

        elif token:
            # K8s mode: Bearer token authentication
            logger.debug(f"Setting up Bearer token authentication (token length: {len(token) if token else 0})")
            os.environ['MLFLOW_TRACKING_TOKEN'] = token
            logger.info("MLflow client configured for Bearer token auth (K8s mode)")

        else:
            # No credentials provided - client will use default/anonymous access
            logger.warning("No authentication credentials provided to MLflow client. "
                          "Client will attempt unauthenticated access.")

        # Create client - it will read credentials from environment variables we just set
        client = MlflowClient(tracking_uri=tracking_uri)

        # Verify credentials were read by inspecting the client's internal state
        # Note: This is for debugging only; MlflowClient doesn't expose credentials directly
        logger.debug("MLflow client created successfully")

        return client
