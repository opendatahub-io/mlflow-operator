"""Artifact action functions.

This module contains all action functions for artifact and model logging operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import tempfile
import os
import mlflow
from sklearn.linear_model import LinearRegression
from ..shared import TestContext

logger = logging.getLogger(__name__)


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