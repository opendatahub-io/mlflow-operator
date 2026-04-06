"""Experiment action functions.

This module contains all action functions for experiment operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import random
import mlflow
from mlflow.exceptions import MlflowException
from mlflow_tests.enums import ResourceType
from ..shared import TestContext
from ..shared.resource_map import get_resource_entry

logger = logging.getLogger(__name__)
random_gen = random.Random()


def action_get_experiment(test_context: TestContext) -> None:
    """Retrieve an experiment and store it in test context.

    Args:
        test_context: Test context containing workspace and resource mappings.
                     Updates active_experiment_id with the retrieved experiment.

    Raises:
        Exception: If experiment retrieval fails (propagated from mlflow).
    """
    logger.info(f"Starting experiment retrieval in workspace '{test_context.active_workspace}'")

    # Most read-path scenarios preselect the exact experiment ID in the test
    # harness. Keep this fallback so callers that only set workspace context
    # still read the baseline primary experiment.
    experiment_id = test_context.active_experiment_id
    if experiment_id is None:
        experiment_id = get_resource_entry(
            test_context.resource_map,
            ResourceType.EXPERIMENTS,
            test_context.active_workspace,
        )["id"]
    logger.debug(f"Retrieving experiment with ID: {experiment_id}")

    experiment = mlflow.get_experiment(experiment_id)
    test_context.active_experiment_id = experiment.experiment_id if experiment else None

    if experiment:
        logger.info(f"Successfully retrieved experiment '{experiment.name}' (ID: {experiment.experiment_id})")
    else:
        logger.warning(f"Experiment retrieval returned None for ID: {experiment_id}")


def action_create_experiment(test_context: TestContext, max_retries: int = 3) -> None:
    """Create a new experiment and store its ID in test context.

    Retries with a new name on conflict (e.g. RESOURCE_ALREADY_EXISTS from a
    soft-deleted experiment that still occupies the unique constraint).

    Args:
        test_context: Test context to update with created experiment ID.
                     Updates active_experiment_id with the new experiment ID.
                     Adds experiment ID to experiments_to_delete for cleanup.
        max_retries: Number of retry attempts on name conflicts.

    Raises:
        Exception: If experiment creation fails for a non-conflict reason,
                   or if all retries are exhausted.
    """
    for attempt in range(1, max_retries + 1):
        experiment_name = f"test-experiment-{random_gen.randint(0, 1_000_000)}"
        logger.info(f"Starting experiment creation in workspace '{test_context.active_workspace}' with name '{experiment_name}' (attempt {attempt}/{max_retries})")

        try:
            experiment_id = mlflow.create_experiment(experiment_name)
        except MlflowException as e:
            if e.error_code == "RESOURCE_ALREADY_EXISTS" and attempt < max_retries:
                logger.warning(f"Experiment name '{experiment_name}' already exists, retrying with a new name")
                continue
            raise

        test_context.active_experiment_id = experiment_id
        logger.info(f"Successfully created experiment '{experiment_name}' with ID: {experiment_id}")

        test_context.add_experiment_for_cleanup(experiment_id, test_context.active_workspace)
        logger.debug(f"Added experiment {experiment_id} to cleanup list for workspace '{test_context.active_workspace}'")
        return


def action_delete_experiment(test_context: TestContext) -> None:
    """
    Deletes an experiment

    Args:
        test_context: Test context to fetch experiment details

    Raises:
        Exception: Delete, or retrieval operations fail (propagated from mlflow).
    """

    # Delete experiment
    logger.debug(f"Deleting experiment {test_context.active_experiment_id}")
    mlflow.delete_experiment(test_context.active_experiment_id)
    logger.info(f"Successfully deleted experiment {test_context.active_experiment_id}")
