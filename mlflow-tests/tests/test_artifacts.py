import logging
import mlflow
from typing import ClassVar

from .shared import UserInfo, TestData, TestStep
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
    validate_model_created,
    validate_local_model_created,
    validate_model_logged,
    validate_model_loaded,
    validate_storage,
    validate_run_created,
    validate_run_ended,
    validate_authentication_denied,
)

import pytest

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)


@pytest.mark.Artifacts
class TestMLflowArtifacts(TestBase):
    """Test Artifact operations with RBAC permissions.

    Tests artifact logging, downloading, model logging/loading, and S3 storage
    verification with different user permission levels (READ, EDIT, MANAGE).
    """

    test_scenarios: ClassVar[list[TestData]] = [
        # Basic artifact workflow tests - CREATE permission
        TestData(
            test_name="User with UPDATE & GET permission can log and download artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.LIST], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact),
                TestStep(action_func=action_list_artifacts, validate_func=validate_artifact_logged),
                TestStep(action_func=action_download_artifact, validate_func=validate_artifact_downloaded),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended)
            ]
        ),
        TestData(
            test_name="User with UPDATE & GET permission can log and load models",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model, validate_func=validate_local_model_created),
                TestStep(action_func=action_log_model, validate_func=validate_model_logged),
                TestStep(action_func=action_load_model, validate_func=validate_model_loaded),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended)
            ]
        ),
        TestData(
            test_name="User with UPDATE permission can verify storage for artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE, KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact),
                TestStep(action_func=action_get_run_info),
                TestStep(action_func=action_end_run, validate_func=validate_storage)
            ]
        ),
        TestData(
            test_name="User with GET permission cannot log models",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS])),
                TestStep(action_func=action_create_model, validate_func=validate_local_model_created),
                TestStep(action_func=action_log_model, validate_func=validate_authentication_denied, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.REGISTERED_MODELS, ResourceType.JOBS]))
            ]
        ),

        # Cross-workspace permission tests
        # Note: These tests verify permission failures when accessing resources in unauthorized workspaces
        TestData(
            test_name="User with GET permission on workspace 1 cannot start run in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_start_run,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with UPDATE permission on workspace 2 cannot log artifacts in workspace 1",
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[1], verbs=[KubeVerb.CREATE, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS])),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact, validate_func=validate_authentication_denied, workspace_to_use=Config.WORKSPACES[0], user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS]))
            ]
        ),

        # Additional negative test cases
        TestData(
            test_name="User with GET permission cannot end run",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS])),
                TestStep(action_func=action_end_run, validate_func=validate_authentication_denied,user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]))
            ]
        ),
        TestData(
            test_name="User with CREATE permission cannot perform operations in different workspace",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_authentication_denied),
                TestStep(action_func=action_create_model, validate_func=validate_authentication_denied)
            ]
        ),
        TestData(
            test_name="User with LIST permission cannot log artifacts without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.LIST], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_authentication_denied),
                TestStep(action_func=action_log_artifact, validate_func=validate_authentication_denied)
            ]
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
        if test_data.user_info:
            verb_names = [verb.value for verb in test_data.user_info.verbs]
            logger.info(f"User verbs: {verb_names}, Resource: {[rt.value for rt in test_data.user_info.resource_types]}")
        if test_data.workspace_to_use:
            logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        # Clear any previous error state before starting new test
        self.test_context.last_error = None

        if test_data.user_info:
            # Step 2: Create user with permissions
            logger.info(f"Step 2: Creating user with {verb_names} permissions on {[rt.value for rt in test_data.user_info.resource_types]} in workspace '{test_data.user_info.workspace}'")
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources
            )
            logger.info(f"Created user: {user_info.uname}")

            # Step 3: Set test context and workspace
            logger.debug(f"Step 3: Setting active user and workspace context")
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client
            logger.debug(f"Using authenticated MLflow client for user: {user_info.uname}")

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")

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


        # Step 4-5: Execute test steps (actions and validations)
        self._execute_test_steps(test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")
