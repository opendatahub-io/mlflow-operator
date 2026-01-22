"""Validation functions for experiment-related operations.

This module contains validation functions that verify the results of experiment
operations (get, create, delete) based on expected permissions and outcomes.
"""

import logging
import mlflow
from ..shared import TestContext
from .validation_utils import validate_action_failed, validate_resource_retrieved_or_created

logger = logging.getLogger(__name__)


def validate_experiment_retrieved(test_context: TestContext) -> None:
    """Validate that an experiment was successfully retrieved.

    Checks that active_experiment_id is populated and no error occurred.

    Args:
        test_context: Test context containing experiment retrieval results.

    Raises:
        AssertionError: If experiment was not retrieved or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_experiment_id",
        resource_type="Experiment",
        operation="retrieval"
    )


def validate_experiment_created(test_context: TestContext) -> None:
    """Validate that an experiment was successfully created.

    Checks that active_experiment_id is populated and no error occurred.

    Args:
        test_context: Test context containing experiment creation results.

    Raises:
        AssertionError: If experiment was not created or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_experiment_id",
        resource_type="Experiment",
        operation="creation"
    )


def validate_experiment_deleted(test_context: TestContext) -> None:
    """Validate that an experiment was successfully deleted.

    Verifies the experiment exists but has lifecycle_stage='deleted'.

    Args:
        test_context: Test context containing deleted experiment ID.

    Raises:
        AssertionError: If experiment deletion verification fails.
    """
    logger.info(f"Validating experiment deletion for user '{test_context.active_user.uname}' in workspace '{test_context.active_workspace}'")

    # Validate no error occurred
    if test_context.last_error is not None:
        logger.error(f"Validation failed: Experiment deletion encountered an error for user '{test_context.active_user.uname}': {test_context.last_error}")
    assert test_context.last_error is None, \
        f"Experiment deletion failed for user {test_context.active_user.uname}: {test_context.last_error}"
    logger.debug("No errors detected during experiment deletion")

    # Validate experiment ID is set
    if test_context.active_experiment_id is None:
        logger.error(f"Validation failed: Experiment ID not set after deletion for user '{test_context.active_user.uname}'")
    assert test_context.active_experiment_id is not None, \
        f"Experiment ID not set after deletion for user: {test_context.active_user.uname}"
    logger.debug(f"Verifying lifecycle stage for experiment {test_context.active_experiment_id}")

    # Verify lifecycle stage
    deleted_experiment = mlflow.get_experiment(test_context.active_experiment_id)
    if deleted_experiment is None:
        logger.error(f"Validation failed: Could not retrieve experiment {test_context.active_experiment_id} after deletion")
    assert deleted_experiment is not None, \
        f"Could not retrieve experiment after deletion for user: {test_context.active_user.uname}"

    actual_stage = deleted_experiment.lifecycle_stage
    if actual_stage != "deleted":
        logger.error(f"Validation failed: Experiment lifecycle stage is '{actual_stage}' instead of 'deleted'")
    assert actual_stage == "deleted", \
        f"Experiment lifecycle stage is '{actual_stage}' instead of 'deleted' " \
        f"for user: {test_context.active_user.uname}"

    logger.info(f"Successfully validated experiment deletion (ID: {test_context.active_experiment_id}, lifecycle_stage: {actual_stage})")


