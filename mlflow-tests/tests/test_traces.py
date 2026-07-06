import logging
from typing import ClassVar

import mlflow
import pytest

from mlflow_tests.enums import KubeVerb, ResourceType

from .actions import action_post_trace_v3_direct
from .base import TestBase
from .constants.config import Config
from .shared import TestData, TestStep, UserInfo
from .shared.resource_map import (
    PRIMARY_RESOURCE_REF,
    PRIMARY_RESOURCE_SLOT,
    SECONDARY_RESOURCE_SLOT,
)
from .validations import validate_authentication_denied, validate_trace_logged

logger = logging.getLogger(__name__)


@pytest.mark.Traces
@pytest.mark.smoke
class TestTraces(TestBase):
    """Test trace logging with experiment-scoped RBAC."""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Agent with GET and UPDATE on one experiment can send traces to that experiment",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.UPDATE],
                resource_types=[ResourceType.EXPERIMENTS],
                resource_names={ResourceType.EXPERIMENTS: [PRIMARY_RESOURCE_REF]},
            ),
            workspace_to_use=Config.WORKSPACES[0],
            resource_slot=PRIMARY_RESOURCE_SLOT,
            test_steps=TestStep(
                action_func=action_post_trace_v3_direct,
                validate_func=validate_trace_logged,
            ),
        ),
        TestData(
            test_name="Agent with GET and UPDATE on one experiment cannot send traces to a different experiment in the same workspace",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.UPDATE],
                resource_types=[ResourceType.EXPERIMENTS],
                resource_names={ResourceType.EXPERIMENTS: [PRIMARY_RESOURCE_REF]},
            ),
            workspace_to_use=Config.WORKSPACES[0],
            resource_slot=SECONDARY_RESOURCE_SLOT,
            test_steps=TestStep(
                action_func=action_post_trace_v3_direct,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="Agent with GET and UPDATE in one workspace cannot send traces in a different workspace",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.UPDATE],
                resource_types=[ResourceType.EXPERIMENTS],
            ),
            workspace_to_use=Config.WORKSPACES[1],
            resource_slot=PRIMARY_RESOURCE_SLOT,
            test_steps=TestStep(
                action_func=action_post_trace_v3_direct,
                validate_func=validate_authentication_denied,
            ),
        ),
    ]

    @pytest.mark.parametrize("test_data", test_scenarios, ids=lambda x: x.test_name)
    def test_trace_logging(
        self,
        create_user_with_permissions,
        test_data: TestData,
    ) -> None:
        """Test agent-style trace emission with experiment-scoped permissions."""
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        if test_data.user_info:
            verb_names = [verb.value for verb in test_data.user_info.verbs]
            logger.info(
                "User verbs: %s, Resource: %s",
                verb_names,
                [rt.value for rt in test_data.user_info.resource_types],
            )
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        self.test_context.last_error = None

        if test_data.user_info:
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources,
                resource_names=test_data.user_info.resource_names,
            )
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            self._set_active_experiment_from_map(
                test_data.workspace_to_use,
                slot=test_data.resource_slot or PRIMARY_RESOURCE_SLOT,
            )
        self.test_context.current_trace_id = None
        self.test_context.current_trace_name = None
        self.test_context.active_trace = None

        self._execute_test_steps(test_data)
