"""MCP Server Registry action functions.

This module contains all action functions for MCP Server Registry operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import uuid

import mlflow
from ..shared import TestContext

logger = logging.getLogger(__name__)


def action_create_mcp_server(test_context: TestContext) -> None:
    """Create a new MCP server (also creates a draft 1.0.0 version) and store its name.

    Args:
        test_context: Test context to update with created server name.
                     Updates active_mcp_server_name with the new server name.
                     Adds server name to mcp_servers_to_delete for cleanup.

    Raises:
        Exception: If server creation fails (propagated from mlflow).
    """
    # Name-scoped RBAC scenarios preselect the exact server name in the test harness
    # (no baseline resource pool to resolve one from) before this action runs.
    server_name = test_context.active_mcp_server_name or (
        f"io.opendatahub.mlflow-tests/test-mcp-server-{uuid.uuid4().hex[:12]}"
    )
    logger.info(f"Starting MCP server creation in workspace '{test_context.active_workspace}' with name '{server_name}'")

    version = mlflow.genai.register_mcp_server(
        server_json={
            "name": server_name,
            "version": "1.0.0",
            "description": "MLflow E2E test MCP server",
        },
        # Explicit so action_delete_mcp_server's dependency on a draft version
        # (a version left ACTIVE would reject delete_mcp_server) isn't tied to
        # register_mcp_server's default.
        status="draft",
        # Skip tool auto-discovery: our payload never sets remotes[], and discovery
        # would otherwise try to reach a URL that does not exist.
        tools=None,
    )
    test_context.active_mcp_server_name = version.name
    logger.info(f"Successfully created MCP server '{version.name}'")

    test_context.add_mcp_server_for_cleanup(version.name, test_context.active_workspace)
    logger.debug(f"Added MCP server {version.name} to cleanup list for workspace '{test_context.active_workspace}'")


def action_create_mcp_server_shell(test_context: TestContext) -> None:
    """Create a bare MCP server with no version, via the low-level client API.

    Exercises MlflowClient.create_mcp_server() directly — the one MCP call
    actually gated by CREATE, unlike register_mcp_server()'s upsert/UPDATE path.

    Args:
        test_context: Test context to update with created server name.
                     Updates active_mcp_server_name with the new server name.
                     Adds server name to mcp_servers_to_delete for cleanup.

    Raises:
        Exception: If server creation fails (propagated from mlflow).
    """
    server_name = test_context.active_mcp_server_name or (
        f"io.opendatahub.mlflow-tests/test-mcp-server-shell-{uuid.uuid4().hex[:12]}"
    )
    server = test_context.user_client.create_mcp_server(
        name=server_name,
        description="MLflow E2E test MCP server (shell, no version)",
    )
    test_context.active_mcp_server_name = server.name
    test_context.add_mcp_server_for_cleanup(server.name, test_context.active_workspace)


def action_get_mcp_server(test_context: TestContext) -> None:
    """Retrieve an MCP server and store it in test context.

    Args:
        test_context: Test context containing the server name to retrieve.
                     Updates active_mcp_server_name with the retrieved server.

    Raises:
        AssertionError: If the server is not found (mlflow returned None).
        Exception: If server retrieval fails (propagated from mlflow).
    """
    requested_name = test_context.active_mcp_server_name
    logger.info(f"Retrieving MCP server '{requested_name}' in workspace '{test_context.active_workspace}'")

    server = mlflow.genai.get_mcp_server(name=requested_name)
    if server is None:
        raise AssertionError(f"MCP server '{requested_name}' not found")

    test_context.active_mcp_server_name = server.name
    logger.info(f"Successfully retrieved MCP server '{server.name}'")


def action_search_mcp_servers(test_context: TestContext) -> None:
    """Search MCP servers and store the results in test context.

    Args:
        test_context: Test context to update with search results.

    Raises:
        Exception: If search fails (propagated from mlflow).
    """
    test_context.mcp_server_search_results = list(mlflow.genai.search_mcp_servers())


def action_delete_mcp_server(test_context: TestContext) -> None:
    """Delete an MCP server.

    Args:
        test_context: Test context containing the server name to delete.

    Raises:
        Exception: If delete operation fails (propagated from mlflow).
    """
    logger.debug(f"Deleting MCP server {test_context.active_mcp_server_name}")
    mlflow.genai.delete_mcp_server(name=test_context.active_mcp_server_name)
    logger.info(f"Successfully deleted MCP server {test_context.active_mcp_server_name}")


def action_register_mcp_server_version(test_context: TestContext) -> None:
    """Add a second version to an existing MCP server.

    Split out of action_create_mcp_server_version_and_endpoint so RBAC scenarios
    can grant the exact verb needed for the access-endpoint call alone, without a
    version-bump call in between confounding which permission actually gated it.

    Args:
        test_context: Test context containing the active server name.
                     Updates active_mcp_server_version.

    Raises:
        Exception: If version creation fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    version = mlflow.genai.register_mcp_server(
        server_json={"name": name, "version": "1.0.1"},
        tools=None,
    )
    test_context.active_mcp_server_version = version.version
    logger.info(f"Created MCP server version '{version.version}' for server '{name}'")


def action_create_mcp_access_endpoint(test_context: TestContext) -> None:
    """Create an access endpoint pinned to the active server's current version.

    Args:
        test_context: Test context containing the active server name and version.
                     Updates active_mcp_access_endpoint_id.

    Raises:
        Exception: If endpoint creation fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    version = test_context.active_mcp_server_version
    endpoint = mlflow.genai.create_mcp_access_endpoint(
        server_name=name,
        url="https://example.invalid/mcp",
        transport_type="streamable-http",
        server_version=version,
    )
    test_context.active_mcp_access_endpoint_id = endpoint.id
    logger.info(f"Created MCP access endpoint '{endpoint.id}' for server '{name}' version '{version}'")


def action_create_mcp_server_version_and_endpoint(test_context: TestContext) -> None:
    """Add a second version to an existing server and create an access endpoint for it.

    Args:
        test_context: Test context containing the active server name.
                     Updates active_mcp_server_version and active_mcp_access_endpoint_id.

    Raises:
        Exception: If version or endpoint creation fails (propagated from mlflow).
    """
    action_register_mcp_server_version(test_context)
    action_create_mcp_access_endpoint(test_context)


def action_get_mcp_access_endpoint(test_context: TestContext) -> None:
    """Retrieve the active MCP access endpoint and store it in test context.

    Args:
        test_context: Test context containing the active server name and endpoint id.
                     Updates active_mcp_access_endpoint.

    Raises:
        Exception: If retrieval fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    endpoint_id = test_context.active_mcp_access_endpoint_id
    endpoint = mlflow.genai.get_mcp_access_endpoint(server_name=name, endpoint_id=endpoint_id)
    test_context.active_mcp_access_endpoint = endpoint
    logger.info(f"Retrieved MCP access endpoint '{endpoint.id}' for server '{name}'")


def action_search_mcp_access_endpoints(test_context: TestContext) -> None:
    """Search MCP access endpoints workspace-wide and store the results in test context.

    Omits server_name so the call hits the workspace-wide search route (gated on
    LIST), not the per-server listing route (gated on GET).

    Args:
        test_context: Test context to update with search results.

    Raises:
        Exception: If search fails (propagated from mlflow).
    """
    test_context.mcp_access_endpoint_search_results = list(mlflow.genai.search_mcp_access_endpoints())


def action_update_mcp_access_endpoint(test_context: TestContext) -> None:
    """Update the URL of the active MCP access endpoint.

    Args:
        test_context: Test context containing the active server name and endpoint id.
                     Updates active_mcp_access_endpoint with the updated object.

    Raises:
        Exception: If update fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    endpoint_id = test_context.active_mcp_access_endpoint_id
    endpoint = mlflow.genai.update_mcp_access_endpoint(
        server_name=name,
        endpoint_id=endpoint_id,
        url="https://example.invalid/mcp-updated",
    )
    test_context.active_mcp_access_endpoint = endpoint
    logger.info(f"Updated MCP access endpoint '{endpoint_id}' for server '{name}'")


def action_delete_mcp_access_endpoint(test_context: TestContext) -> None:
    """Delete the active MCP access endpoint.

    Args:
        test_context: Test context containing the active server name and endpoint id.

    Raises:
        Exception: If delete operation fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    endpoint_id = test_context.active_mcp_access_endpoint_id
    logger.debug(f"Deleting MCP access endpoint {endpoint_id} for server {name}")
    mlflow.genai.delete_mcp_access_endpoint(server_name=name, endpoint_id=endpoint_id)
    logger.info(f"Successfully deleted MCP access endpoint {endpoint_id} for server {name}")
