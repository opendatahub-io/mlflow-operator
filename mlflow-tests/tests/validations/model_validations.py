"""Validation functions for registered model operations.

This module contains validation functions that verify the results of registered model
operations (get, create, delete) based on expected permissions and outcomes.
"""

import logging
import mlflow
from mlflow.exceptions import MlflowException
from ..shared import TestContext
from .validation_utils import validate_action_failed, validate_resource_retrieved_or_created

logger = logging.getLogger(__name__)


def validate_model_retrieved(test_context: TestContext) -> None:
    """Validate that a registered model was successfully retrieved.

    Checks that active_model_name is populated and no error occurred.

    Args:
        test_context: Test context containing model retrieval results.

    Raises:
        AssertionError: If model was not retrieved or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_model_name",
        resource_type="Registered model",
        operation="retrieval"
    )


def validate_model_created(test_context: TestContext) -> None:
    """Validate that a registered model was successfully created.

    Checks that active_model_name is populated and no error occurred.

    Args:
        test_context: Test context containing model creation results.

    Raises:
        AssertionError: If model was not created or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_model_name",
        resource_type="Registered model",
        operation="creation"
    )


def validate_model_deleted(test_context: TestContext) -> None:
    """Validate that a registered model was successfully deleted.

    Verifies the model no longer exists or cannot be retrieved.

    Args:
        test_context: Test context containing deleted model name.

    Raises:
        AssertionError: If model deletion verification fails.
    """
    logger.info(f"Validating registered model deletion for user '{test_context.active_user.uname}' in workspace '{test_context.active_workspace}'")

    # Validate no error occurred
    if test_context.last_error is not None:
        logger.error(f"Validation failed: Registered model deletion encountered an error for user '{test_context.active_user.uname}': {test_context.last_error}")
    assert test_context.last_error is None, \
        f"Registered model deletion failed for user {test_context.active_user.uname}: {test_context.last_error}"
    logger.debug("No errors detected during registered model deletion")

    # Validate model name is set
    if test_context.active_model_name is None:
        logger.error(f"Validation failed: Model name not set after deletion for user '{test_context.active_user.uname}'")
    assert test_context.active_model_name is not None, \
        f"Model name not set after deletion for user: {test_context.active_user.uname}"
    logger.debug(f"Verifying deletion status for model {test_context.active_model_name}")

    # Verify model no longer exists
    deletion_verified = False
    try:
        deleted_model = test_context.user_client.get_registered_model(test_context.active_model_name)
        # If we get here without exception, the model still exists (unexpected)
        logger.error(f"Validation failed: Model {test_context.active_model_name} still exists after deletion")
    except MlflowException as e:
        # Expected: Model should not be found
        error_message = str(e)
        if "RESOURCE_DOES_NOT_EXIST" in error_message or "does not exist" in error_message.lower():
            deletion_verified = True
            logger.debug(f"Model deletion verified - model not found as expected")
        else:
            logger.error(f"Validation failed: Unexpected MLflow error during deletion verification: {error_message}")
    except Exception as e:
        logger.error(f"Validation failed: Unexpected error during deletion verification: {e}")

    assert deletion_verified, \
        f"Model deletion verification failed - model {test_context.active_model_name} still exists " \
        f"for user: {test_context.active_user.uname}"

    logger.info(f"Successfully validated registered model deletion (name: {test_context.active_model_name})")


