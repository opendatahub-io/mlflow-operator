import logging
import random
from typing import ClassVar

import mlflow
import pytest

from .shared import UserInfo, TestData, TestStep, TestContext
from .constants.config import Config
from .actions import (
    action_create_mcp_server,
    action_get_mcp_server,
    action_delete_mcp_server,
    action_create_mcp_server_version_and_endpoint,
)
from .validations.mcp_validations import (
    validate_mcp_server_retrieved,
    validate_mcp_server_created,
    validate_mcp_server_deleted,
    validate_mcp_server_version_and_endpoint_created,
)
from .validations import validate_authentication_denied

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)

_random_gen = random.Random()

# Name-scoped RBAC scenarios need a fixed name to grant on the K8s Role before the
# server exists, so these are literals rather than resolved from a baseline resource
# pool (MCP servers have none, unlike experiments/registered models).
SCOPED_MCP_SERVER_NAME = f"io.opendatahub.mlflow-tests/scoped-mcp-server-{_random_gen.randint(0, 10_000)}"
OTHER_MCP_SERVER_NAME = f"io.opendatahub.mlflow-tests/other-mcp-server-{_random_gen.randint(0, 10_000)}"


def _seed_mcp_server_name(name: str):
    """Return an action that preselects the MCP server name for the next create step."""

    def action(test_context: TestContext) -> None:
        test_context.active_mcp_server_name = name

    action.__name__ = f"seed_mcp_server_name_{name}"
    return action


@pytest.mark.MCPRegistry
@pytest.mark.smoke
class TestMCPServers(TestBase):
    """Test MCP Server Registry RBAC"""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate that user with GET permission can get MCP server",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_get_mcp_server,
                    validate_func=validate_mcp_server_retrieved,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with GET permission cannot create MCP server",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="Validate that user with GET permission on workspace 1 cannot get MCP server in workspace 2",
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    workspace_to_use=Config.WORKSPACES[1],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[1],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_get_mcp_server,
                    validate_func=validate_authentication_denied,
                    workspace_to_use=Config.WORKSPACES[1],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with GET permission scoped to one MCP server can get that server",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=_seed_mcp_server_name(SCOPED_MCP_SERVER_NAME)),
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_get_mcp_server,
                    validate_func=validate_mcp_server_retrieved,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                        resource_names={ResourceType.MCP_SERVERS: [SCOPED_MCP_SERVER_NAME]},
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with GET permission scoped to one MCP server cannot get a different server in the same workspace",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=_seed_mcp_server_name(OTHER_MCP_SERVER_NAME)),
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_get_mcp_server,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                        resource_names={ResourceType.MCP_SERVERS: [SCOPED_MCP_SERVER_NAME]},
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with CREATE permission can create MCP server",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_mcp_server_created,
            ),
        ),
        TestData(
            test_name="Validate that user with GET, CREATE and DELETE permissions can delete MCP server",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.CREATE, KubeVerb.DELETE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_delete_mcp_server, validate_func=validate_mcp_server_deleted),
            ],
        ),
        TestData(
            test_name="Validate that user with CREATE permission on workspace 1 cannot create MCP server in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_authentication_denied,
            ),
        ),
        # Additional negative test cases
        TestData(
            test_name="User with GET permission cannot delete MCP server",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_delete_mcp_server,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User with CREATE permission cannot delete MCP server without DELETE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_delete_mcp_server, validate_func=validate_authentication_denied),
            ],
        ),
        TestData(
            test_name="User with DELETE permission cannot create MCP server without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.DELETE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="User with DELETE permission cannot get MCP server without GET permission",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_get_mcp_server,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.DELETE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User with UPDATE permission cannot create MCP server without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="User with LIST permission cannot delete MCP server without DELETE permission",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_delete_mcp_server,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.LIST],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Full MCP server lifecycle: create, add version and access endpoint, then delete",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.CREATE, KubeVerb.UPDATE, KubeVerb.LIST, KubeVerb.DELETE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(
                    action_func=action_create_mcp_server_version_and_endpoint,
                    validate_func=validate_mcp_server_version_and_endpoint_created,
                ),
                TestStep(action_func=action_delete_mcp_server, validate_func=validate_mcp_server_deleted),
            ],
        ),
    ]

    @pytest.mark.parametrize('test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_mcp_server(self, create_user_with_permissions, test_data: TestData):
        """Test MCP server operations with user permissions.

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
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources,
                resource_names=test_data.user_info.resource_names,
            )
            logger.info(f"Created user: {user_info.uname}")
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")

        self._execute_test_steps(test_data=test_data)
