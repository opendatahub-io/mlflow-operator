import logging
import mlflow
from mlflow import MlflowClient
from typing import ClassVar

from .shared import UserInfo, TestData
from .constants.config import Config
from .actions import (
    action_start_run,
    action_end_run,
    action_create_temp_artifact,
    action_log_artifact,
    action_list_artifacts,
    action_download_artifact,
    action_create_model,
    action_log_model,
    action_load_model,
    action_get_run_info,
)
from .validations import (
    validate_artifact_logged,
    validate_artifact_downloaded,
    validate_model_logged,
    validate_model_loaded,
    validate_storage,
    validate_run_created,
    validate_action_failed,
)

import pytest

from mlflow_tests.enums import ResourceType, UserRole
from .base import TestBase

logger = logging.getLogger(__name__)


@pytest.mark.Artifacts
class TestMLflowArtifacts(TestBase):
    """Test Artifact operations with RBAC permissions.

    Tests artifact logging, downloading, model logging/loading, and S3 storage
    verification with different user permission levels (READ, EDIT, MANAGE).
    """

    test_scenarios: ClassVar[list[TestData]] = [
        # Basic artifact workflow tests - EDIT permission
        TestData(
            test_name="User with EDIT permission can log and download artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
                action_list_artifacts,
                action_download_artifact,
                action_end_run,
            ],
            validate_func=[
                validate_run_created,
                validate_artifact_logged,
                validate_artifact_downloaded,
            ],
        ),
        TestData(
            test_name="User with EDIT permission can log and load models",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_model,
                action_log_model,
                action_load_model,
                action_end_run,
            ],
            validate_func=[
                validate_run_created,
                validate_model_logged,
                validate_model_loaded,
            ],
        ),
        TestData(
            test_name="User with EDIT permission can verify storage for artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
                action_get_run_info,
                action_end_run,
            ],
            validate_func=[
                validate_run_created,
                validate_storage,
            ],
        ),

        TestData(
            test_name="User with READ permission cannot log artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
            ],
            validate_func=validate_action_failed,
        ),
        TestData(
            test_name="User with READ permission cannot log models",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_model,
                action_log_model,
            ],
            validate_func=validate_action_failed,
        ),

        # Cross-workspace permission tests
        # Note: These tests verify permission failures when accessing resources in unauthorized workspaces
        TestData(
            test_name="User with READ permission on workspace 1 cannot start run in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.READ, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=action_start_run,
            validate_func=validate_action_failed,
        ),
        TestData(
            test_name="User with EDIT permission on workspace 1 cannot log artifacts in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.EDIT, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
            ],
            validate_func=validate_action_failed,
        ),

        # MANAGE permission tests
        TestData(
            test_name="User with MANAGE permission can log and download artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
                action_list_artifacts,
                action_download_artifact,
                action_end_run,
            ],
            validate_func=[
                validate_run_created,
                validate_artifact_logged,
                validate_artifact_downloaded,
            ],
        ),
        TestData(
            test_name="User with MANAGE permission can log and load models",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[0],
            action_func=[
                action_start_run,
                action_create_model,
                action_log_model,
                action_load_model,
                action_end_run,
            ],
            validate_func=[
                validate_run_created,
                validate_model_logged,
                validate_model_loaded,
            ],
        ),
        TestData(
            test_name="User with MANAGE permission on workspace 1 cannot log artifacts in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], role=UserRole.MANAGE, resource_type=ResourceType.EXPERIMENTS),
            workspace_to_use=Config.WORKSPACES[1],
            action_func=[
                action_start_run,
                action_create_temp_artifact,
                action_log_artifact,
            ],
            validate_func=validate_action_failed,
        ),
    ]

    @pytest.mark.parametrize(
        'test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_mlflow_artifacts(self, create_user_with_permissions, test_data: TestData) -> None:
        """Test artifact operations with user permissions.

        Executes action(s) (if provided) and validates the result based on user permissions.
        Supports both single actions and sequences of actions for complex workflows.

        Args:
            create_user_with_permissions: Fixture to create test users with specific permissions.
            test_data: Test configuration containing user info, actions, and validations.

        Raises:
            AssertionError: If any validation fails.
        """
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info(f"User role: {test_data.user_info.role.value}, Resource: {test_data.user_info.resource_type.value}")
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        # Clear any previous error state before starting new test
        self.test_context.last_error = None

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

        # Safely retrieve experiment ID with boundary check
        experiments_in_workspace = self.resource_map[ResourceType.EXPERIMENTS].get(test_data.workspace_to_use, [])

        # Handle both scalar strings and sequences (list/tuple)
        if not experiments_in_workspace:
            # Empty/None value
            logger.warning(f"No experiments found in workspace '{test_data.workspace_to_use}', using None")
            self.test_context.active_experiment_id = None
        elif isinstance(experiments_in_workspace, str):
            # Scalar string case - use directly
            self.test_context.active_experiment_id = experiments_in_workspace
            logger.info(f"Set active experiment ID to: {self.test_context.active_experiment_id}")
        elif isinstance(experiments_in_workspace, (list, tuple)) and len(experiments_in_workspace) > 0:
            # Sequence case - use first element
            self.test_context.active_experiment_id = experiments_in_workspace[0]
            logger.info(f"Set active experiment ID to: {self.test_context.active_experiment_id}")
        else:
            # Empty sequence or unexpected type
            logger.warning(f"No experiments found in workspace '{test_data.workspace_to_use}', using None")
            self.test_context.active_experiment_id = None

        mlflow.set_workspace(self.test_context.active_workspace)
        logger.info(f"Set active workspace to: {test_data.workspace_to_use}")
        logger.debug(f"Created authenticated MLflow client for user: {user_info.uname}")

        # Step 4: Execute action(s) if provided
        self._execute_actions(test_data)

        # Step 5: Validate the result(s)
        self._execute_validations(test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")
