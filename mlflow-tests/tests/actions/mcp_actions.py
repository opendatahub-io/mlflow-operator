"""MCP Server Registry action functions.

This module contains all action functions for MCP Server Registry operations.
Each action accepts only test_context as an argument and modifies it appropriately.
"""

import logging
import random

import mlflow
from ..shared import TestContext

logger = logging.getLogger(__name__)
random_gen = random.Random()


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
        f"io.opendatahub.mlflow-tests/test-mcp-server-{random_gen.randint(0, 10_000)}"
    )
    logger.info(f"Starting MCP server creation in workspace '{test_context.active_workspace}' with name '{server_name}'")

    version = mlflow.genai.register_mcp_server(
        server_json={
            "name": server_name,
            "version": "1.0.0",
            "description": "MLflow E2E test MCP server",
        },
        # Skip tool auto-discovery: our payload never sets remotes[], and discovery
        # would otherwise try to reach a URL that does not exist.
        tools=None,
    )
    test_context.active_mcp_server_name = version.name
    logger.info(f"Successfully created MCP server '{version.name}'")

    test_context.add_mcp_server_for_cleanup(version.name, test_context.active_workspace)
    logger.debug(f"Added MCP server {version.name} to cleanup list for workspace '{test_context.active_workspace}'")


def action_get_mcp_server(test_context: TestContext) -> None:
    """Retrieve an MCP server and store it in test context.

    Args:
        test_context: Test context containing the server name to retrieve.
                     Updates active_mcp_server_name with the retrieved server.

    Raises:
        Exception: If server retrieval fails (propagated from mlflow).
    """
    logger.info(f"Retrieving MCP server '{test_context.active_mcp_server_name}' in workspace '{test_context.active_workspace}'")

    server = mlflow.genai.get_mcp_server(name=test_context.active_mcp_server_name)
    test_context.active_mcp_server_name = server.name if server else None

    if server:
        logger.info(f"Successfully retrieved MCP server '{server.name}'")
    else:
        logger.warning("MCP server retrieval returned None")


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
    # Depends on register_mcp_server's default status="draft": a version left in
    # ACTIVE status would cause delete_mcp_server to be rejected.
    logger.debug(f"Deleting MCP server {test_context.active_mcp_server_name}")
    mlflow.genai.delete_mcp_server(name=test_context.active_mcp_server_name)
    logger.info(f"Successfully deleted MCP server {test_context.active_mcp_server_name}")


def action_create_mcp_server_version_and_endpoint(test_context: TestContext) -> None:
    """Add a second version to an existing server and create an access endpoint for it.

    Args:
        test_context: Test context containing the active server name.
                     Updates active_mcp_server_version and active_mcp_access_endpoint_id.

    Raises:
        Exception: If version or endpoint creation fails (propagated from mlflow).
    """
    name = test_context.active_mcp_server_name
    version = mlflow.genai.register_mcp_server(
        server_json={"name": name, "version": "1.0.1"},
        tools=None,
    )
    test_context.active_mcp_server_version = version.version
    logger.info(f"Created MCP server version '{version.version}' for server '{name}'")

    endpoint = mlflow.genai.create_mcp_access_endpoint(
        server_name=name,
        url="https://example.invalid/mcp",
        transport_type="streamable-http",
        server_version=version.version,
    )
    test_context.active_mcp_access_endpoint_id = endpoint.id
    logger.info(f"Created MCP access endpoint '{endpoint.id}' for server '{name}' version '{version.version}'")
