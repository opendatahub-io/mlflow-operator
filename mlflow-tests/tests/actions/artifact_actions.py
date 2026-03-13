"""Artifact action functions.

This module contains all action functions for artifact and model logging operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import tempfile
import os
import time
import mlflow
import pytest
from kubernetes import client
from sklearn.linear_model import LinearRegression
from mlflow_tests.utils.client import ClientManager
from ..shared import TestContext
from ..constants.config import Config

logger = logging.getLogger(__name__)


def _get_k8s_clients():
    """Get Kubernetes API clients."""
    ClientManager.load_k8s_config()
    return client.CoreV1Api(), client.CustomObjectsApi()


def action_start_run(test_context: TestContext) -> None:
    """Start a new MLflow run and store run ID in test context.

    Args:
        test_context: Test context containing active_experiment_id.
                     Updates current_run_id with the new run ID.
                     Adds run ID to runs_to_delete for cleanup.

    Raises:
        Exception: If run creation fails (propagated from mlflow).
    """
    logger.info(f"Starting MLflow run in experiment {test_context.active_experiment_id}")

    run = mlflow.start_run(experiment_id=test_context.active_experiment_id)
    test_context.current_run_id = run.info.run_id
    logger.info(f"Successfully started run {test_context.current_run_id}")

    # Add to cleanup tracker with workspace context
    test_context.add_run_for_cleanup(test_context.current_run_id, test_context.active_workspace)
    logger.debug(f"Added run {test_context.current_run_id} to cleanup list for workspace '{test_context.active_workspace}'")


def action_end_run(test_context: TestContext) -> None:
    """End the current MLflow run.

    Args:
        test_context: Test context containing current_run_id.
                     Does not modify any fields.

    Raises:
        Exception: If ending run fails (propagated from mlflow).
    """
    logger.info(f"Ending MLflow run {test_context.current_run_id}")

    mlflow.end_run()
    logger.info(f"Successfully ended run {test_context.current_run_id}")


def action_create_temp_artifact(test_context: TestContext) -> None:
    """Create a temporary artifact file and store its path and content.

    Args:
        test_context: Test context to update.
                     Updates temp_artifact_path with file path.
                     Updates temp_artifact_content with file content.

    Raises:
        Exception: If file creation fails (propagated from OS).
    """
    logger.info("Creating temporary artifact file")

    content = "test artifact content"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        test_context.temp_artifact_path = f.name
        test_context.temp_artifact_content = content

    logger.info(f"Successfully created temporary artifact at {test_context.temp_artifact_path}")


def action_log_artifact(test_context: TestContext) -> None:
    """Log an artifact to the current run.

    Args:
        test_context: Test context containing temp_artifact_path and current_run_id.
                     Does not modify any fields.

    Raises:
        Exception: If artifact logging fails (propagated from mlflow).
    """
    logger.info(f"Logging artifact {test_context.temp_artifact_path} to run {test_context.current_run_id}")

    mlflow.log_artifact(test_context.temp_artifact_path)
    logger.info(f"Successfully logged artifact {os.path.basename(test_context.temp_artifact_path)}")


def action_list_artifacts(test_context: TestContext) -> None:
    """List artifacts for the current run and store the list.

    Args:
        test_context: Test context containing current_run_id.
                     Updates artifact_list with the list of artifacts.

    Raises:
        Exception: If artifact listing fails (propagated from mlflow).
    """
    logger.info(f"Listing artifacts for run {test_context.current_run_id}")

    # Use the authenticated client from test context
    test_context.artifact_list = test_context.user_client.list_artifacts(test_context.current_run_id)
    logger.info(f"Successfully listed {len(test_context.artifact_list)} artifact(s)")


def action_download_artifact(test_context: TestContext) -> None:
    """Download an artifact from the current run and store the download path.

    Args:
        test_context: Test context containing current_run_id and temp_artifact_path.
                     Updates downloaded_path with the local download path.

    Raises:
        Exception: If artifact download fails (propagated from mlflow).
    """
    artifact_path = os.path.basename(test_context.temp_artifact_path)
    logger.info(f"Downloading artifact '{artifact_path}' from run {test_context.current_run_id}")

    test_context.downloaded_path = mlflow.artifacts.download_artifacts(
        run_id=test_context.current_run_id,
        artifact_path=artifact_path
    )
    logger.info(f"Successfully downloaded artifact to {test_context.downloaded_path}")


def action_create_model(test_context: TestContext) -> None:
    """Create a simple sklearn model and store it in test context.

    Args:
        test_context: Test context to update.
                     Updates model with a trained LinearRegression model.

    Raises:
        Exception: If model creation fails (propagated from sklearn).
    """
    logger.info("Creating and training sklearn LinearRegression model")

    model = LinearRegression()
    # Train on simple data: y = 2*x + 1
    X = [[1], [2], [3]]
    y = [3, 5, 7]
    model.fit(X, y)

    test_context.model = model
    logger.info("Successfully created and trained model")


def action_log_model(test_context: TestContext) -> None:
    """Log a model to MLflow and store the model URI.

    Args:
        test_context: Test context containing model and current_run_id.
                     Updates model_uri with the logged model's URI.

    Raises:
        Exception: If model logging fails (propagated from mlflow).
    """
    logger.info(f"Logging model to run {test_context.current_run_id}")

    model_info = mlflow.sklearn.log_model(test_context.model, "model")
    test_context.model_uri = model_info.model_uri
    logger.info(f"Successfully logged model with URI: {test_context.model_uri}")


def action_load_model(test_context: TestContext) -> None:
    """Load a model from MLflow using the stored model URI.

    Args:
        test_context: Test context containing model_uri.
                     Updates model with the loaded model.

    Raises:
        Exception: If model loading fails (propagated from mlflow).
    """
    logger.info(f"Loading model from URI: {test_context.model_uri}")

    test_context.model = mlflow.sklearn.load_model(test_context.model_uri)
    logger.info("Successfully loaded model")


def action_get_run_info(test_context: TestContext) -> None:
    """Get run info and store artifact location in test context.

    Args:
        test_context: Test context containing current_run_id.
                     Updates artifact_location with the run's artifact URI.

    Raises:
        Exception: If run retrieval fails (propagated from mlflow).
    """
    logger.info(f"Retrieving run info for run_id: {test_context.current_run_id}")

    run = mlflow.get_run(test_context.current_run_id)
    test_context.artifact_location = run.info.artifact_uri

    logger.info(f"Successfully retrieved run info, artifact location: {test_context.artifact_location}")


def action_create_artifact_connection_secret(test_context: TestContext) -> None:
    """Create the mlflow-artifact-connection Secret for custom artifact storage.

    Creates a Secret with S3 credentials in the active workspace namespace.

    Args:
        test_context: Test context containing active_workspace.
                     Updates expected_artifact_bucket with the bucket name.

    Raises:
        Exception: If secret creation fails.
    """
    if not Config.S3_URL:
        pytest.skip("Requires S3 backend (MLFLOW_S3_ENDPOINT_URL)")

    namespace = test_context.active_workspace
    secret_name = "mlflow-artifact-connection"
    bucket_name = Config.S3_BUCKET or "mlpipeline"

    logger.info(f"Creating artifact connection secret '{secret_name}' in namespace '{namespace}'")

    core_v1_api, _ = _get_k8s_clients()

    secret = client.V1Secret(
        api_version="v1",
        kind="Secret",
        metadata=client.V1ObjectMeta(name=secret_name, namespace=namespace),
        string_data={
            "AWS_ACCESS_KEY_ID": Config.AWS_ACCESS_KEY or "minio",
            "AWS_SECRET_ACCESS_KEY": Config.AWS_SECRET_KEY or "minio123",
            "AWS_S3_BUCKET": bucket_name,
            "AWS_S3_ENDPOINT": Config.S3_URL or "http://minio-service:9000",
        }
    )

    try:
        core_v1_api.create_namespaced_secret(namespace=namespace, body=secret)
        logger.info(f"Successfully created secret '{secret_name}' in namespace '{namespace}'")
    except client.ApiException as e:
        if e.status == 409:
            logger.info(f"Secret '{secret_name}' already exists in namespace '{namespace}'")
        else:
            raise

    test_context.expected_artifact_bucket = bucket_name
    test_context.add_secret_for_cleanup(secret_name, namespace)


def action_wait_for_mlflowconfig_active(test_context: TestContext) -> None:
    """Poll until the MLflowConfig artifact override is picked up by the server.

    Creates and deletes temporary experiments to check if the artifact URI
    reflects the expected S3 path, retrying with 1s backoff.
    """
    path = test_context.expected_artifact_path
    logger.info(f"Polling for MLflowConfig to become active (path: '{path}')")

    last_error: Exception | None = None
    for i in range(10):
        try:
            exp_id = mlflow.create_experiment(f"mlflowconfig-probe-{i}")
            test_context.add_experiment_for_cleanup(exp_id, test_context.active_workspace)
            location = mlflow.get_experiment(exp_id).artifact_location

            if location and location.startswith("s3://") and path in location:
                logger.info(f"MLflowConfig active after {i + 1} attempt(s)")
                return
        except Exception as exc:
            last_error = exc

        time.sleep(1)

    detail = f" Last error: {last_error}" if last_error else ""
    raise TimeoutError(f"MLflowConfig artifact override not active after 10s.{detail}")


def action_create_mlflowconfig(test_context: TestContext) -> None:
    """Create an MLflowConfig CR with custom artifact path.

    Args:
        test_context: Test context containing active_workspace.
                     Updates expected_artifact_path with the configured path.

    Raises:
        Exception: If MLflowConfig creation fails.
    """
    namespace = test_context.active_workspace
    config_name = "mlflow"
    artifact_path = "custom-artifacts"

    logger.info(f"Creating MLflowConfig '{config_name}' in namespace '{namespace}' with path '{artifact_path}'")

    _, custom_api = _get_k8s_clients()

    mlflowconfig = {
        "apiVersion": "mlflow.kubeflow.org/v1",
        "kind": "MLflowConfig",
        "metadata": {
            "name": config_name,
            "namespace": namespace
        },
        "spec": {
            "artifactRootSecret": "mlflow-artifact-connection",
            "artifactRootPath": artifact_path
        }
    }

    try:
        custom_api.create_namespaced_custom_object(
            group="mlflow.kubeflow.org",
            version="v1",
            namespace=namespace,
            plural="mlflowconfigs",
            body=mlflowconfig
        )
        logger.info(f"Successfully created MLflowConfig '{config_name}' in namespace '{namespace}'")
    except client.ApiException as e:
        if e.status == 409:
            logger.info(f"MLflowConfig '{config_name}' already exists in namespace '{namespace}'")
        else:
            raise

    test_context.expected_artifact_path = artifact_path
    test_context.add_mlflowconfig_for_cleanup(config_name, namespace)
