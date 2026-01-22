"""Model action functions.

This module contains all action functions for registered model operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import random
import mlflow
from mlflow_tests.enums import ResourceType
from ..shared import TestContext

logger = logging.getLogger(__name__)
random_gen = random.Random()


def action_get_registered_model(test_context: TestContext) -> None:
    """Retrieve a registered model and store it in test context.

    Args:
        test_context: Test context containing workspace and resource mappings.
                     Updates active_model_name with the retrieved model.

    Raises:
        Exception: If model retrieval fails (propagated from mlflow).
    """
    logger.info(f"Starting registered model retrieval in workspace '{test_context.active_workspace}'")

    # Set the workspace context in MLflow before making API calls
    logger.debug(f"Setting MLflow workspace to: '{test_context.active_workspace}'")
    mlflow.set_workspace(test_context.active_workspace)

    model_name = test_context.resource_map[ResourceType.REGISTERED_MODELS][test_context.active_workspace]
    logger.debug(f"Retrieving registered model with name: {model_name}")

    model = test_context.user_client.get_registered_model(model_name)
    test_context.active_model_name = model.name if model else None

    if model:
        logger.info(f"Successfully retrieved registered model '{model.name}'")
    else:
        logger.warning(f"Registered model retrieval returned None for name: {model_name}")


def action_create_registered_model(test_context: TestContext) -> None:
    """Create a new registered model and store its name in test context.

    Args:
        test_context: Test context to update with created model name.
                     Updates active_model_name with the new model name.
                     Adds model name to models_to_delete for cleanup.

    Raises:
        Exception: If model creation fails (propagated from mlflow).
    """
    model_name = f"test-model-{random_gen.randint(0, 10_000)}"
    logger.info(f"Starting registered model creation in workspace '{test_context.active_workspace}' with name '{model_name}'")

    # Set the workspace context in MLflow before making API calls
    logger.debug(f"Setting MLflow workspace to: '{test_context.active_workspace}'")
    mlflow.set_workspace(test_context.active_workspace)

    model = test_context.user_client.create_registered_model(model_name)
    test_context.active_model_name = model.name
    logger.info(f"Successfully created registered model '{model_name}'")

    # Add to cleanup tracker with workspace context
    test_context.add_model_for_cleanup(model.name, test_context.active_workspace)
    logger.debug(f"Added model {model.name} to cleanup list for workspace '{test_context.active_workspace}'")


def action_delete_registered_model(test_context: TestContext) -> None:
    """Delete a registered model

    Args:
        test_context: Test context to fetch model details

    Raises:
        Exception: If delete operations fail (propagated from mlflow).
    """

    # Set the workspace context in MLflow before making API calls
    logger.debug(f"Setting MLflow workspace to: '{test_context.active_workspace}'")
    mlflow.set_workspace(test_context.active_workspace)

    # Delete registered model
    logger.debug(f"Step 2: Deleting registered model {test_context.active_model_name}")
    test_context.user_client.delete_registered_model(test_context.active_model_name)
    logger.info(f"Successfully deleted registered model {test_context.active_model_name}")
