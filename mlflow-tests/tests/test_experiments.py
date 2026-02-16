import logging
import mlflow
from mlflow import MlflowClient
from typing import ClassVar

from .shared import UserInfo, TestData, TestStep
from .constants.config import Config
from .actions import (
    action_get_experiment,
    action_create_experiment,
    action_delete_experiment,
)
from .validations.experiment_validations import (
    validate_experiment_retrieved,
    validate_experiment_created,
    validate_experiment_deleted,
)
from .validations import validate_authentication_denied

import pytest

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)


@pytest.mark.Experiments
class TestExperiments(TestBase):
    """Test Experiments"""


    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate that user with GET permission can get experiment",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_get_experiment,
                validate_func=validate_experiment_retrieved
            )
        ),
        TestData(
            test_name="Validate that user with GET permission cannot create experiment",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_experiment,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="Validate that user with GET permission on workspace 2 cannot get experiment in workspace 1",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_get_experiment,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="Validate that user with CREATE permission can create experiment",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_experiment,
                validate_func=validate_experiment_created
            )
        ),
        TestData(
            test_name="Validate that user with GET, CREATE and DELETE permissions can delete experiment",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.CREATE, KubeVerb.DELETE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_create_experiment),
                TestStep(action_func=action_delete_experiment, validate_func=validate_experiment_deleted)
            ]
        ),
        TestData(
            test_name="Validate that user with CREATE permission on workspace 1, cannot create experiment in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_create_experiment,
                validate_func=validate_authentication_denied
            )
        ),

        # Additional negative test cases
        TestData(
            test_name="User with GET permission cannot delete experiment",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_delete_experiment,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with CREATE permission cannot delete experiment without DELETE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_create_experiment, validate_func=validate_experiment_created),
                TestStep(action_func=action_delete_experiment, validate_func=validate_authentication_denied)
            ]
        ),
        TestData(
            test_name="User with UPDATE permission cannot create experiment without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_experiment,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with LIST permission cannot delete experiment without DELETE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.LIST], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_delete_experiment,
                validate_func=validate_authentication_denied
            )
        ),
    ]

    @pytest.mark.parametrize(
        'test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_experiment(self, create_user_with_permissions, test_data: TestData):
        """Test experiment operations with user permissions.

        Executes action (if provided) and validates the result based on user permissions.
        """
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        if test_data.user_info:
            verb_names = [verb.value for verb in test_data.user_info.verbs]
            logger.info(f"User verbs: {verb_names}, Resource: {[rt.value for rt in test_data.user_info.resource_types]}")
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

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
            logger.debug(f"Created authenticated MLflow client for user: {user_info.uname}")

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")

        # Step 4-5: Execute test steps (actions and validations)
        self._execute_test_steps(test_data=test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")