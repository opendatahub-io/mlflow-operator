import logging
import mlflow
import random
from mlflow import MlflowClient
from typing import ClassVar

from .shared import UserInfo, TestData
from .constants.config import Config
from .actions import (
    action_get_registered_model,
    action_create_registered_model,
    action_delete_registered_model,
)
from .validations.model_validations import (
    validate_model_retrieved,
    validate_model_created,
    validate_model_deleted,
    validate_action_failed,
)

import pytest

from mlflow_tests.enums import ResourceType, UserRole
from .base import TestBase

logger = logging.getLogger(__name__)


@pytest.mark.Models
class TestRegisteredModels(TestBase):
    """Test Registered Models RBAC"""


    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate that user with READ permission can get registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=action_get_registered_model,
            validate_func=validate_model_retrieved,
        ),
        TestData(
            test_name="Validate that user with READ permission cannot create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=action_create_registered_model,
            validate_func=validate_action_failed,
        ),
        TestData(
            test_name="Validate that user with READ permission on workspace 1 cannot get registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=action_get_registered_model,
            validate_func=validate_action_failed,
        ),
        TestData(
            test_name="Validate that user with EDIT permission can create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=action_create_registered_model,
            validate_func=validate_model_created,
        ),
        TestData(
            test_name="Validate that user with EDIT permission can delete registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_create_registered_model,
                action_delete_registered_model
                ],
            validate_func=validate_model_deleted,
        ),
        TestData(
            test_name="Validate that user with EDIT permission on workspace 1 cannot create registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=action_create_registered_model,
            validate_func=validate_action_failed,
        ),
        TestData(
            test_name="Validate that user with MANAGE permission can create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=action_create_registered_model,
            validate_func=validate_model_created,
        ),
        TestData(
            test_name="Validate that user with MANAGE permission can delete registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_create_registered_model,
                action_delete_registered_model
                ],
            validate_func=validate_model_deleted,
        ),
        TestData(
            test_name="Validate that user with MANAGE permission on workspace 1 cannot create registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.REGISTERED_MODELS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=action_create_registered_model,
            validate_func=validate_action_failed,
        ),
    ]

    @pytest.mark.parametrize(
        'test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_registered_model(self, create_user_with_permissions, test_data: TestData):
        """Test registered model operations with user permissions.

        Executes action (if provided) and validates the result based on user permissions.
        """
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info(f"User role: {test_data.user_info.role.value}, Resource: {test_data.user_info.resource_type.value}")
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        # Step 2: Create user with permissions
        logger.info(f"Step 2: Creating user with {test_data.user_info.role.value} permissions on {test_data.user_info.resource_type.value} in workspace '{test_data.user_info.workspace}'")
        user_info: UserInfo = create_user_with_permissions(
            workspace=test_data.user_info.workspace,
            user_role=test_data.user_info.role,
            resource_type=test_data.user_info.resource_type
        )
        logger.info(f"Created user: {user_info.uname}")

        # Step 3: Set test context and workspace
        logger.debug(f"Step 3: Setting active user and workspace context")
        self.test_context.active_user = user_info
        self.test_context.user_client = MlflowClient()
        self.test_context.active_workspace = test_data.workspace_to_use
        mlflow.set_workspace(self.test_context.active_workspace)
        logger.info(f"Set active workspace to: {test_data.workspace_to_use}")
        logger.debug(f"Created authenticated MLflow client for user: {user_info.uname}")

        # Step 4: Execute action if provided
        self._execute_actions(test_data=test_data)

        # Step 5: Validate the result
        self._execute_validations(test_data=test_data)
