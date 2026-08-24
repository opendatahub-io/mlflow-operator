import logging
import uuid
from typing import ClassVar

import mlflow
import pytest

from .shared import UserInfo, TestData, TestStep, TestContext
from .constants.config import Config
from .actions import (
    action_create_mcp_server,
    action_create_mcp_server_shell,
    action_get_mcp_server,
    action_delete_mcp_server,
    action_search_mcp_servers,
    action_register_mcp_server_version,
    action_create_mcp_access_endpoint,
    action_create_mcp_server_version_and_endpoint,
    action_get_mcp_access_endpoint,
    action_search_mcp_access_endpoints,
    action_update_mcp_access_endpoint,
    action_delete_mcp_access_endpoint,
)
from .validations.mcp_validations import (
    validate_mcp_server_retrieved,
    validate_mcp_server_created,
    validate_mcp_server_deleted,
    validate_mcp_server_version_and_endpoint_created,
    validate_mcp_server_search_excludes_other_workspace,
    validate_mcp_access_endpoint_created,
    validate_mcp_access_endpoint_retrieved,
    validate_mcp_access_endpoint_search_excludes_other_workspace,
    validate_mcp_access_endpoint_updated,
    validate_mcp_access_endpoint_deleted,
)
from .validations import validate_authentication_denied, validate_no_error

from mlflow_tests.enums import ResourceType, KubeVerb
from .base import TestBase

logger = logging.getLogger(__name__)

# Name-scoped RBAC scenarios need a fixed name to grant on the K8s Role before the
# server exists, so these are literals rather than resolved from a baseline resource
# pool (MCP servers have none, unlike experiments/registered models).
SCOPED_MCP_SERVER_NAME = f"io.opendatahub.mlflow-tests/scoped-mcp-server-{uuid.uuid4().hex[:12]}"
OTHER_MCP_SERVER_NAME = f"io.opendatahub.mlflow-tests/other-mcp-server-{uuid.uuid4().hex[:12]}"
DENIED_MCP_SERVER_NAME = f"io.opendatahub.mlflow-tests/denied-mcp-server-{uuid.uuid4().hex[:12]}"


def _seed_mcp_server_name(name: str):
    """Return an action that preselects the MCP server name for the next create step."""

    def action(test_context: TestContext) -> None:
        test_context.active_mcp_server_name = name

    action.__name__ = f"seed_mcp_server_name_{name}"
    return action


@pytest.mark.MCPRegistry
@pytest.mark.smoke
class TestMCPServers(TestBase):
    """Test MCP Server Registry RBAC

    Note on verb semantics: mlflow.genai.register_mcp_server() is a single
    upsert call — it creates the parent MCPServer automatically if `name` is
    new, or appends a version if it already exists. There is no separate
    client-visible "create the parent" step (unlike registered models, where
    create_registered_model() and create_model_version() are two independent
    calls). Per a reviewed decision in mlflow_kubernetes_plugins
    (auth/rules_v3_14.py::apply_mcp_registry_deltas), this whole call is
    gated on UPDATE only, regardless of whether `name` already exists.
    KubeVerb.CREATE is NOT the permission that controls "create a brand-new
    MCP server" via register_mcp_server() — it instead guards the low-level
    MlflowClient.create_mcp_server() call (a bare server with no version,
    never touched by register_mcp_server()'s upsert path); see the "...via
    the direct client API" scenarios below for that coverage. So throughout
    the rest of this file, UPDATE is the verb granted wherever a test needs
    to actually create an MCP server via register_mcp_server() (including as
    a setup step for GET/DELETE scenarios); CREATE only appears where a test
    deliberately wants a verb that should NOT be sufficient for that path.

    Note on access endpoint verb semantics: per the same apply_mcp_registry_deltas
    table, DELETE is reserved exclusively for the top-level MCPServer object's own
    delete route. Every operation on a resource nested inside a server — a
    version, a tag, an alias, or an access endpoint — is gated on UPDATE,
    regardless of whether the HTTP method is POST, PATCH, or DELETE. So
    mlflow.genai.delete_mcp_access_endpoint() requires UPDATE, not DELETE. The
    access endpoint scenarios below grant exactly the verb needed for the
    operation under test (rather than a bundled all-verbs grant) specifically so
    a swapped or dropped verb check in the plugin fails the relevant test instead
    of being masked by over-permissioning.
    """

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
                        verbs=[KubeVerb.UPDATE],
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
                        verbs=[KubeVerb.UPDATE],
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
                        verbs=[KubeVerb.UPDATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                        resource_names={ResourceType.MCP_SERVERS: [SCOPED_MCP_SERVER_NAME]},
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
                        verbs=[KubeVerb.UPDATE],
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
            test_name="Validate that user with UPDATE permission scoped to one MCP server cannot create a different server",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=_seed_mcp_server_name(DENIED_MCP_SERVER_NAME)),
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.UPDATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                        resource_names={ResourceType.MCP_SERVERS: [SCOPED_MCP_SERVER_NAME]},
                    ),
                ),
            ],
        ),
        TestData(
            # Looks backwards, but is correct: see the class docstring above.
            # register_mcp_server() only ever checks UPDATE (upsert semantics);
            # CREATE alone is not sufficient here. The low-level create path
            # (where CREATE *is* the right verb) is covered separately below
            # by the "...via the direct client API" scenarios.
            test_name="Validate that user with CREATE permission cannot register an MCP server (add a version) without UPDATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="Validate that user with CREATE permission can create a bare MCP server via the direct client API",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server_shell,
                validate_func=validate_mcp_server_created,
            ),
        ),
        TestData(
            test_name="Validate that user with UPDATE permission cannot create a bare MCP server via the direct client API without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server_shell,
                validate_func=validate_authentication_denied,
            ),
        ),
        TestData(
            test_name="Validate that user with GET, UPDATE and DELETE permissions can delete MCP server",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.DELETE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_delete_mcp_server, validate_func=validate_mcp_server_deleted),
            ],
        ),
        TestData(
            test_name="Validate that user with UPDATE permission on workspace 1 cannot create MCP server in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.MCP_SERVERS]),
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
                        verbs=[KubeVerb.UPDATE],
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
            test_name="User with UPDATE permission cannot delete MCP server without DELETE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_delete_mcp_server, validate_func=validate_authentication_denied),
            ],
        ),
        TestData(
            test_name="User with DELETE permission cannot create MCP server without UPDATE permission",
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
                        verbs=[KubeVerb.UPDATE],
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
            # Looks backwards, but is correct: see the class docstring above.
            # This is the positive counterpart to the CREATE-cannot-create test —
            # UPDATE alone is sufficient to create a brand-new MCP server, by design.
            test_name="User with UPDATE permission can create MCP server",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.MCP_SERVERS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=TestStep(
                action_func=action_create_mcp_server,
                validate_func=validate_mcp_server_created,
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
                        verbs=[KubeVerb.UPDATE],
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
            test_name="Validate that MCP server search results are scoped to the active workspace",
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    workspace_to_use=Config.WORKSPACES[1],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[1],
                        verbs=[KubeVerb.UPDATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(
                    action_func=action_search_mcp_servers,
                    validate_func=validate_mcp_server_search_excludes_other_workspace,
                    workspace_to_use=Config.WORKSPACES[0],
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
                verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.LIST, KubeVerb.DELETE],
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
        # Access endpoint RBAC scenarios (verb-isolated coverage).
        #
        # MCP access endpoints are authorized on the parent ResourceType.MCP_SERVERS
        # resource — there is no separate resource type for endpoints. Per
        # mlflow_kubernetes_plugins/auth/rules_v3_14.py::apply_mcp_registry_deltas:
        #   POST   .../<name>/endpoints          -> UPDATE
        #   GET    .../<name>/endpoints/<id>      -> GET
        #   GET    .../endpoints (workspace-wide) -> LIST
        #   PATCH  .../<name>/endpoints/<id>      -> UPDATE
        #   DELETE .../<name>/endpoints/<id>      -> UPDATE (not DELETE)
        #
        # Unlike the bundled lifecycle scenario above (which grants every verb at
        # once and therefore can't prove which permission actually gated the
        # access-endpoint call), each scenario below grants exactly the verb the
        # operation under test needs, so a swapped or dropped verb check in the
        # plugin fails the relevant test instead of being masked by
        # over-permissioning.
        TestData(
            test_name="Validate that user with UPDATE permission can create an MCP access endpoint",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
            ],
        ),
        TestData(
            test_name="Validate that user with CREATE permission cannot create an MCP access endpoint without UPDATE permission",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(
                    action_func=action_create_mcp_access_endpoint,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.CREATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with GET permission can get an MCP access endpoint",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_get_mcp_access_endpoint,
                    validate_func=validate_mcp_access_endpoint_retrieved,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with UPDATE permission cannot get an MCP access endpoint without GET permission",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(action_func=action_get_mcp_access_endpoint, validate_func=validate_authentication_denied),
            ],
        ),
        TestData(
            test_name="Validate that MCP access endpoint search results are scoped to the active workspace",
            test_steps=[
                TestStep(
                    action_func=action_create_mcp_server,
                    validate_func=validate_mcp_server_created,
                    workspace_to_use=Config.WORKSPACES[1],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[1],
                        verbs=[KubeVerb.UPDATE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_search_mcp_access_endpoints,
                    validate_func=validate_mcp_access_endpoint_search_excludes_other_workspace,
                    workspace_to_use=Config.WORKSPACES[0],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.LIST],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="Validate that user with UPDATE permission can update an MCP access endpoint",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(action_func=action_update_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_updated),
            ],
        ),
        TestData(
            test_name="User with GET permission cannot update MCP access endpoint without UPDATE permission",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_update_mcp_access_endpoint,
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
            test_name="User with DELETE permission cannot update MCP access endpoint without UPDATE permission",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_update_mcp_access_endpoint,
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
            # Looks backwards, but is correct: see the class docstring above and
            # mlflow_kubernetes_plugins/auth/rules_v3_14.py::apply_mcp_registry_deltas.
            # DELETE .../endpoints/<id> is gated on UPDATE, not DELETE.
            test_name="Validate that user with UPDATE permission can delete an MCP access endpoint",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(action_func=action_delete_mcp_access_endpoint, validate_func=validate_no_error),
                # Post-delete state check, run as a GET-only user so this proves the
                # endpoint is actually gone rather than re-using the UPDATE-only
                # session above (which would fail with PERMISSION_DENIED on GET).
                TestStep(
                    action_func=action_get_mcp_access_endpoint,
                    validate_func=validate_mcp_access_endpoint_deleted,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
            ],
        ),
        TestData(
            test_name="User with GET permission cannot delete MCP access endpoint without UPDATE permission",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_delete_mcp_access_endpoint,
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
            # The critical case this suite was missing: delete_mcp_access_endpoint
            # is gated on UPDATE, not DELETE (see apply_mcp_registry_deltas in
            # mlflow_kubernetes_plugins). A user with only DELETE must still be
            # denied here — if this ever passes, the plugin's verb mapping (or
            # this test) has regressed.
            test_name="User with DELETE permission alone cannot delete MCP access endpoint (requires UPDATE, not DELETE)",
            user_info=UserInfo(
                workspace=Config.WORKSPACES[0],
                verbs=[KubeVerb.UPDATE],
                resource_types=[ResourceType.MCP_SERVERS],
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(action_func=action_create_mcp_server, validate_func=validate_mcp_server_created),
                TestStep(action_func=action_register_mcp_server_version, validate_func=validate_no_error),
                TestStep(action_func=action_create_mcp_access_endpoint, validate_func=validate_mcp_access_endpoint_created),
                TestStep(
                    action_func=action_delete_mcp_access_endpoint,
                    validate_func=validate_authentication_denied,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.DELETE],
                        resource_types=[ResourceType.MCP_SERVERS],
                    ),
                ),
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
