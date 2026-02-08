"""Kubernetes ServiceAccount management."""

import logging
import random
import string
import time

from kubernetes import client
from kubernetes.client.rest import ApiException
from typing import Any

logger = logging.getLogger(__name__)


class ServiceAccountManager:
    """Class for managing Kubernetes ServiceAccounts."""

    def __init__(self, core_v1_api: client.CoreV1Api):
        """Initialize the ServiceAccountManager with a Kubernetes CoreV1 API client.

        Args:
            core_v1_api: Kubernetes CoreV1 API client
        """
        self.core_v1_api = core_v1_api

    def _create_service_account(
        self, name: str, namespace: str
    ) -> None:
        """Create a Kubernetes ServiceAccount.

        Args:
            name: ServiceAccount name
            namespace: Namespace for the ServiceAccount

        Raises:
            ApiException: If creation fails
        """
        service_account = client.V1ServiceAccount(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace)
        )

        try:
            logger.info(f"Creating service account '{name}' in namespace '{namespace}'")
            self.core_v1_api.create_namespaced_service_account(
                namespace=namespace, body=service_account
            )
            # Wait for service account to be ready before proceeding
            time.sleep(1)

            # Verify the service account exists and is ready
            self.core_v1_api.read_namespaced_service_account(name=name, namespace=namespace)
            logger.info(f"Service account '{name}' created successfully")
            return None
        except ApiException as e:
            if e.status == 409:  # Already exists, return existing one
                logger.debug(f"Service account '{name}' already exists in namespace '{namespace}'")
                return self.core_v1_api.read_namespaced_service_account(
                    name=name, namespace=namespace
                )
            else:
                logger.error(f"Failed to create service account '{name}' in namespace '{namespace}': {e}")
                raise Exception(f"Failed to create service account due to: {e}")

    def _get_token_via_token_request(
        self,
        service_account_name: str,
        namespace: str,
        max_retries: int = 5,
        retry_delay: float = 1.0,
        token_expiration_seconds: int = 3600
    ) -> str:
        """Get a service account token using the TokenRequest API.

        Args:
            service_account_name: ServiceAccount name to get token for
            namespace: Namespace of the ServiceAccount
            max_retries: Maximum number of retry attempts
            retry_delay: Delay in seconds between retries
            token_expiration_seconds: Token validity duration in seconds (default: 1 hour)

        Returns:
            JWT token string ready for Kubernetes API authentication

        Raises:
            ApiException: If token request fails
            ValueError: If token is empty or invalid

        Note:
            Uses the modern TokenRequest API which is more secure than creating secrets.
            Tokens are short-lived and don't persist in etcd.
        """
        current_delay = retry_delay

        for attempt in range(max_retries):
            try:
                logger.debug(f"Requesting token for service account '{service_account_name}' (attempt {attempt + 1}/{max_retries})")

                # Create TokenRequest object
                token_request = client.V1TokenRequestSpec(
                        expiration_seconds=token_expiration_seconds,
                        # No audiences specified - will use default API server audience
                        audiences=[]
                )

                # Make the token request using CoreV1Api
                response = self.core_v1_api.create_namespaced_service_account_token(
                    name=service_account_name,
                    namespace=namespace,
                    body=token_request
                )

                if not response or not response.status or not response.status.token:
                    if attempt < max_retries - 1:
                        logger.debug(f"Token request returned empty response, retrying after {current_delay}s...")
                        time.sleep(current_delay)
                        current_delay *= 1.5  # Exponential backoff
                        continue
                    raise ValueError(f"TokenRequest API returned empty token for service account '{service_account_name}'")

                token = response.status.token

                # Validate token format
                if not token or len(token) < 10:
                    if attempt < max_retries - 1:
                        logger.debug(f"Retrieved token appears invalid (length: {len(token) if token else 0}), retrying after {current_delay}s...")
                        time.sleep(current_delay)
                        current_delay *= 1.5
                        continue
                    raise ValueError(f"Retrieved token appears invalid (length: {len(token) if token else 0})")

                # Validate JWT format
                parts = token.split('.')
                if len(parts) != 3:
                    if attempt < max_retries - 1:
                        logger.debug(f"Token is not JWT format (has {len(parts)} parts, expected 3), retrying after {current_delay}s...")
                        time.sleep(current_delay)
                        current_delay *= 1.5
                        continue
                    logger.error(f"Token is not JWT format (has {len(parts)} parts, expected 3)")
                    logger.error("MLflow requires JWT tokens for authentication")
                    # Still return it - let MLflow handle the error with better context

                logger.info(f"Successfully retrieved token via TokenRequest API for '{service_account_name}' (length: {len(token)}, expires in {token_expiration_seconds}s)")
                return token

            except ApiException as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to get token via TokenRequest API for '{service_account_name}' after {max_retries} retries: {e}")
                    raise
                else:
                    logger.debug(f"TokenRequest attempt {attempt + 1} failed: {e}, retrying after {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= 1.5

        # This should not be reached, but added for safety
        raise RuntimeError(f"Failed to retrieve token via TokenRequest API for '{service_account_name}' after {max_retries} attempts")

    def create_sa_and_get_token(self, sa_name: str, other_info: str) -> tuple[str, str]:
        """Create a service account and return its authentication token using TokenRequest API.

        Args:
            sa_name: ServiceAccount name to create
            other_info: Namespace for the service account

        Returns:
            Tuple of (service_account_name, token) for Kubernetes API authentication

        Raises:
            ApiException: If service account creation or token request fails
            ValueError: If token is empty or invalid

        Note:
            This method uses the modern TokenRequest API approach:
            1. Creates the service account and waits for it to be ready
            2. Uses TokenRequest API to get a short-lived JWT token
            3. No secrets are created, improving security and reducing cleanup overhead
        """
        namespace = other_info

        logger.info(f"Starting service account creation workflow for '{sa_name}' in namespace '{namespace}' (using TokenRequest API)")

        try:
            # Step 1: Create service account and wait for it to be ready
            self._create_service_account(sa_name, namespace)

            # Step 2: Get token using TokenRequest API
            # Wait a brief moment for the service account to be fully propagated
            time.sleep(0.5)

            token = self._get_token_via_token_request(sa_name, namespace)

            logger.info(f"Successfully completed service account creation workflow for '{sa_name}' using TokenRequest API")
            return sa_name, token

        except Exception as e:
            logger.error(f"Service account creation workflow failed for '{sa_name}' in namespace '{namespace}': {e}")
            raise

    def delete_service_account(self, sa_name: str, namespace: str = None) -> None:
        """Delete a Kubernetes ServiceAccount.

        Args:
            sa_name: ServiceAccount name to delete
            namespace: Namespace of the ServiceAccount (optional, will look up if not provided)

        Raises:
            ApiException: If deletion fails

        Note:
            This method only needs to clean up the service account itself.
            No secrets to clean up when using TokenRequest API.
        """
        if not namespace:
            logger.warning(f"Namespace not provided for service account '{sa_name}' deletion. Unable to delete.")
            return

        try:
            logger.info(f"Deleting service account '{sa_name}' in namespace '{namespace}'")

            # Delete the service account
            self.core_v1_api.delete_namespaced_service_account(
                name=sa_name,
                namespace=namespace
            )
            logger.info(f"Successfully deleted service account '{sa_name}' from namespace '{namespace}'")

        except ApiException as e:
            if e.status == 404:
                logger.debug(f"Service account '{sa_name}' already deleted or not found in namespace '{namespace}'")
            else:
                error_msg = f"Failed to delete service account '{sa_name}': {e}"
                logger.error(error_msg)
                raise

    def _get_random_string(self):
        """Generate a random string for unique naming."""
        letters = string.ascii_letters
        random_string = ''.join(random.choices(letters, k=4))
        return random_string.lower()
