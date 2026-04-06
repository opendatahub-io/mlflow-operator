import logging
import mlflow
from typing import ClassVar

from .shared import UserInfo, TestData, TestStep
from .constants.config import Config
from .actions import (
    action_get_registered_model,
    action_create_registered_model,
    action_delete_registered_model,
)
from .shared.resource_map import (
    PRIMARY_RESOURCE_REF,
    PRIMARY_RESOURCE_SLOT,
    SECONDARY_RESOURCE_SLOT,
)
from .validations.model_validations import (
    validate_model_retrieved,
    validate_model_created,
    validate_model_deleted,
)
from .validations import validate_authentication_denied

import pytest

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)


@pytest.mark.Models
@pytest.mark.smoke
class TestRegisteredModels(TestBase):
    """Test Registered Models RBAC"""


    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate that user with GET permission can get registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_get_registered_model,
                validate_func=validate_model_retrieved
            )
        ),
        TestData(
            test_name="Validate that user with GET permission cannot create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="Validate that user with GET permission on workspace 1 cannot get registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_get_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="Validate that user with GET permission scoped to one registered model can get that model",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET],
                resource_types=[ResourceType.REGISTERED_MODELS],
                resource_names={ResourceType.REGISTERED_MODELS: [PRIMARY_RESOURCE_REF]},
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_get_registered_model,
                validate_func=validate_model_retrieved,
            ),
        ),
        TestData(
            test_name="Validate that user with GET permission scoped to one registered model cannot get a different model in the same workspace",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET],
                resource_types=[ResourceType.REGISTERED_MODELS],
                resource_names={ResourceType.REGISTERED_MODELS: [PRIMARY_RESOURCE_REF]},
            ),
            workspace_to_use=Config.WORKSPACES[0],
            resource_slot=SECONDARY_RESOURCE_SLOT,
            test_steps=TestStep(
                action_func=action_get_registered_model,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="Validate that user with CREATE permission can create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_model_created
            )
        ),
        TestData(
            test_name="Validate that user with GET, CREATE and DELETE permissions can delete registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.CREATE, KubeVerb.DELETE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_create_registered_model, validate_func=validate_model_created),
                TestStep(action_func=action_delete_registered_model, validate_func=validate_model_deleted)
            ]
        ),
        TestData(
            test_name="Validate that user with CREATE permission on workspace 1 cannot create registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        # Preserve the existing explicit standalone scenarios in addition to the
        # surrounding negative cases so the suite does not reduce prior coverage.
        TestData(
            test_name="Validate that user with CREATE permission can create registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_model_created
            )
        ),
        TestData(
            test_name="Validate that user with GET, CREATE and DELETE permissions can delete registered model",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.CREATE, KubeVerb.DELETE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_create_registered_model, validate_func=validate_model_created),
                TestStep(action_func=action_delete_registered_model, validate_func=validate_model_deleted)
            ]
        ),
        TestData(
            test_name="Validate that user with CREATE permission on workspace 1 cannot create registered model in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        # Additional negative test cases
        TestData(
            test_name="User with GET permission cannot delete registered model",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(
                    action_func=action_create_registered_model,
                    validate_func=validate_model_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.REGISTERED_MODELS],
                    ),
                ),
                TestStep(
                    action_func=action_delete_registered_model,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.REGISTERED_MODELS],
                    ),
                ),
            ]
        ),
        TestData(
            test_name="User with CREATE permission cannot delete registered model without DELETE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_create_registered_model, validate_func=validate_model_created),
                TestStep(action_func=action_delete_registered_model, validate_func=validate_authentication_denied)
            ]
        ),
        TestData(
            test_name="User with DELETE permission cannot create registered model without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.DELETE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with DELETE permission cannot get registered model without GET permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.DELETE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_get_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with UPDATE permission cannot create registered model without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.REGISTERED_MODELS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = TestStep(
                action_func=action_create_registered_model,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with LIST permission cannot delete registered model without DELETE permission",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(
                    action_func=action_create_registered_model,
                    validate_func=validate_model_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.REGISTERED_MODELS],
                    ),
                ),
                TestStep(
                    action_func=action_delete_registered_model,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.LIST],
                        resource_types=[ResourceType.REGISTERED_MODELS],
                    ),
                ),
            ]
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
        if test_data.user_info:
            verb_names = [verb.value for verb in test_data.user_info.verbs]
            logger.info(f"User verbs: {verb_names}, Resource: {[rt.value for rt in test_data.user_info.resource_types]}")
        if test_data.workspace_to_use:
            logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        if test_data.user_info:
            # Step 2: Create user with permissions
            logger.info(f"Step 2: Creating user with {verb_names} permissions on {[rt.value for rt in test_data.user_info.resource_types]} in workspace '{test_data.user_info.workspace}'")
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources,
                resource_names=test_data.user_info.resource_names,
            )
            logger.info(f"Created user: {user_info.uname}")
            logger.debug(f"Created authenticated MLflow client for user: {user_info.uname}")
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client

        if test_data.workspace_to_use:
            # Step 3: Set test context and workspace
            logger.debug("Step 3: Setting workspace context")
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")
            # Read-path scenarios use the baseline resource selected here; create-step
            # scenarios may overwrite active_model_name later in the test flow.
            self._set_active_model_from_map(
                test_data.workspace_to_use,
                slot=test_data.resource_slot or PRIMARY_RESOURCE_SLOT,
            )

        # Step 4-5: Execute test steps (actions and validations)
        self._execute_test_steps(test_data=test_data)
