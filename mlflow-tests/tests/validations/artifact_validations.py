"""Validation functions for artifact-related operations.

This module contains validation functions that verify the results of artifact
operations (logging, listing, downloading, model operations) based on expected outcomes.
"""

import logging
import os
from ..shared import TestContext
from ..constants.config import Config

logger = logging.getLogger(__name__)


def validate_artifact_logged(test_context: TestContext) -> None:
    """Validate that an artifact was successfully logged.

    Checks that artifact_list contains the expected artifact after logging.

    Args:
        test_context: Test context containing artifact_list and temp_artifact_path.

    Raises:
        AssertionError: If artifact was not logged or list is empty.
    """
    logger.info("Validating artifact was successfully logged")

    # Validate artifact_list is set and not empty
    assert test_context.artifact_list is not None, \
        "Artifact list not retrieved after logging"
    assert len(test_context.artifact_list) > 0, \
        "Artifact list is empty after logging artifact"

    # Validate expected artifact exists in list
    artifact_name = os.path.basename(test_context.temp_artifact_path)
    artifact_names = [artifact.path for artifact in test_context.artifact_list]

    assert artifact_name in artifact_names, \
        f"Expected artifact '{artifact_name}' not found in list: {artifact_names}"

    logger.info(f"Successfully validated artifact '{artifact_name}' was logged")


def validate_artifact_downloaded(test_context: TestContext) -> None:
    """Validate that an artifact was successfully downloaded.

    Checks that downloaded_path exists and contains expected content.

    Args:
        test_context: Test context containing downloaded_path and temp_artifact_content.

    Raises:
        AssertionError: If download failed or content doesn't match.
    """
    logger.info(f"Validating artifact download at {test_context.downloaded_path}")

    # Validate download path is set
    assert test_context.downloaded_path is not None, \
        "Download path not set after downloading artifact"

    # Validate file exists
    assert os.path.exists(test_context.downloaded_path), \
        f"Downloaded artifact file does not exist at: {test_context.downloaded_path}"

    # Validate content matches
    with open(test_context.downloaded_path, 'r') as f:
        content = f.read()

    assert content == test_context.temp_artifact_content, \
        f"Downloaded content '{content}' does not match original '{test_context.temp_artifact_content}'"

    logger.info("Successfully validated artifact download and content match")


def validate_model_logged(test_context: TestContext) -> None:
    """Validate that a model was successfully logged to MLflow.

    Checks that model_uri is set and follows expected format.

    Args:
        test_context: Test context containing model_uri.

    Raises:
        AssertionError: If model_uri is not set or invalid.
    """
    logger.info("Validating model was successfully logged")

    # Validate model_uri is set
    assert test_context.model_uri is not None, \
        "Model URI not set after logging model"

    # Validate URI format (should start with 'runs:/' for logged models)
    assert test_context.model_uri.startswith('models:/'), \
        f"Invalid model URI format: {test_context.model_uri}"

    logger.info(f"Successfully validated model logging (URI: {test_context.model_uri})")


def validate_model_loaded(test_context: TestContext) -> None:
    """Validate that a model was successfully loaded from MLflow.

    Checks that model is set and can make predictions.

    Args:
        test_context: Test context containing loaded model.

    Raises:
        AssertionError: If model is not loaded or cannot predict.
    """
    logger.info("Validating model was successfully loaded")

    # Validate model is set
    assert test_context.model is not None, \
        "Model not loaded from MLflow"

    # Validate model can make predictions
    test_input = [[4]]
    try:
        prediction = test_context.model.predict(test_input)
        logger.debug(f"Model prediction for {test_input}: {prediction}")
    except Exception as e:
        raise AssertionError(f"Loaded model cannot make predictions: {e}")

    # For our LinearRegression y = 2*x + 1, input 4 should predict ~9
    expected_prediction = 9.0
    tolerance = 0.1
    actual_prediction = prediction[0]

    assert abs(actual_prediction - expected_prediction) < tolerance, \
        f"Model prediction {actual_prediction} differs from expected {expected_prediction}"

    logger.info(f"Successfully validated model loading and prediction (predicted: {actual_prediction})")


def validate_storage(test_context: TestContext) -> None:
    """Validate that artifacts are stored in either file or S3.

    Checks that artifact_location uses S3 or file protocol.

    Args:
        test_context: Test context containing artifact_location.

    Raises:
        AssertionError: If artifact_location is not set.
    """
    logger.info("Validating storage configuration")

    # Validate artifact_location is set
    assert test_context.artifact_location is not None, \
        "Artifact location not set in test context"
    logger.debug(f"Artifact location: {test_context.artifact_location}")

    if Config.ARTIFACT_STORAGE == "s3" and not Config.SERVE_ARTIFACTS:
        # Validate S3 protocol
        is_s3 = test_context.artifact_location.startswith('s3://')
        assert is_s3, \
            f"Expected S3 storage (s3://...), but got: {test_context.artifact_location}"
    else:
        is_file = test_context.artifact_location.startswith('mlflow-artifacts:')
        assert is_file, \
            f"Expected File storage (mlflow-artifacts:/...), but got: {test_context.artifact_location}"

    logger.info(f"Successfully validated S3 storage: {test_context.artifact_location}")


def validate_run_created(test_context: TestContext) -> None:
    """Validate that an MLflow run was successfully created.

    Checks that current_run_id is populated and no error occurred.

    Args:
        test_context: Test context containing current_run_id.

    Raises:
        AssertionError: If run was not created or an error occurred.
    """
    logger.info("Validating MLflow run was successfully created")

    # Validate no error occurred
    assert test_context.last_error is None, \
        f"Run creation failed: {test_context.last_error}"
    logger.debug("No errors detected during run creation")

    # Validate run ID is set
    assert test_context.current_run_id is not None, \
        "Run ID not set after starting run"

    logger.info(f"Successfully validated run creation (run_id: {test_context.current_run_id})")