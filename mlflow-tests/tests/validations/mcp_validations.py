"""Validation functions for MCP Server Registry operations.

This module contains validation functions that verify the results of MCP server
operations (get, create, delete, version/endpoint creation) based on expected
permissions and outcomes.
"""

import logging

import mlflow
from mlflow.exceptions import MlflowException
from ..shared import TestContext, ErrorResponse
from .validation_utils import validate_resource_retrieved_or_created

logger = logging.getLogger(__name__)


def validate_mcp_server_retrieved(test_context: TestContext) -> None:
    """Validate that an MCP server was successfully retrieved.

    Checks that active_mcp_server_name is populated and no error occurred.

    Args:
        test_context: Test context containing server retrieval results.

    Raises:
        AssertionError: If server was not retrieved or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_mcp_server_name",
        resource_type="MCP server",
        operation="retrieval",
    )


def validate_mcp_server_created(test_context: TestContext) -> None:
    """Validate that an MCP server was successfully created.

    Checks that active_mcp_server_name is populated and no error occurred.

    Args:
        test_context: Test context containing server creation results.

    Raises:
        AssertionError: If server was not created or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_mcp_server_name",
        resource_type="MCP server",
        operation="creation",
    )


def validate_mcp_server_deleted(test_context: TestContext) -> None:
    """Validate that an MCP server was successfully deleted.

    Verifies the server no longer exists or cannot be retrieved.

    Args:
        test_context: Test context containing deleted server name.

    Raises:
        AssertionError: If server deletion verification fails.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"
    logger.info(f"Validating MCP server deletion for user '{user_name}' in workspace '{test_context.active_workspace}'")

    # Validate no error occurred
    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        logger.error(f"Validation failed: MCP server deletion encountered an error for user '{user_name}': {error_response.error.code} - {error_response.error.message}")
        raise AssertionError(
            f"MCP server deletion failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"
        )
    logger.debug("No errors detected during MCP server deletion")

    # Validate server name is set
    if test_context.active_mcp_server_name is None:
        logger.error(f"Validation failed: Server name not set after deletion for user '{user_name}'")
        raise AssertionError(
            f"MCP server name not set after deletion for user: {user_name}"
        )
    logger.debug(f"Verifying deletion status for MCP server {test_context.active_mcp_server_name}")

    # Verify server no longer exists
    try:
        mlflow.genai.get_mcp_server(test_context.active_mcp_server_name)
    except MlflowException as e:
        if e.error_code == "RESOURCE_DOES_NOT_EXIST":
            logger.debug("MCP server deletion verified - server not found as expected")
            logger.info(f"Successfully validated MCP server deletion (name: {test_context.active_mcp_server_name})")
            return
        if e.error_code == "PERMISSION_DENIED":
            raise AssertionError(
                f"MCP server deletion verification failed for user {user_name} - caller lacks "
                f"permission to verify deletion of {test_context.active_mcp_server_name}: {e}"
            ) from e
        raise AssertionError(
            f"MCP server deletion verification failed for user {user_name} - unexpected error while "
            f"checking {test_context.active_mcp_server_name}: {e}"
        ) from e

    # No exception means the server is still retrievable, so deletion did not take effect.
    raise AssertionError(
        f"MCP server deletion verification failed - server {test_context.active_mcp_server_name} still exists "
        f"for user: {user_name}"
    )


def validate_mcp_server_search_excludes_other_workspace(test_context: TestContext) -> None:
    """Validate that MCP server search results do not leak servers from other workspaces.

    Args:
        test_context: Test context containing search results and the name of an
                     MCP server created in a different workspace.

    Raises:
        AssertionError: If search failed or the other-workspace server leaked through.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        raise AssertionError(
            f"MCP server search failed for user {user_name} in workspace "
            f"'{test_context.active_workspace}': {error_response.error.code} - {error_response.error.message}"
        )

    results = test_context.mcp_server_search_results or []
    found_names = {server.name for server in results}
    if test_context.active_mcp_server_name in found_names:
        raise AssertionError(
            f"MCP server search in workspace '{test_context.active_workspace}' leaked server "
            f"'{test_context.active_mcp_server_name}' created in another workspace"
        )

    logger.info(
        f"Successfully validated MCP server search workspace isolation "
        f"(workspace '{test_context.active_workspace}', {len(results)} result(s), user {user_name})"
    )


def validate_mcp_server_version_and_endpoint_created(test_context: TestContext) -> None:
    """Validate that an MCP server version and access endpoint were successfully created.

    Checks that active_mcp_server_version and active_mcp_access_endpoint_id are
    populated and no error occurred.

    Args:
        test_context: Test context containing version/endpoint creation results.

    Raises:
        AssertionError: If creation failed or identifiers were not set.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"
    workspace = test_context.active_workspace

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        raise AssertionError(
            f"MCP server version/endpoint creation failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"
        )

    if test_context.active_mcp_server_version is None:
        raise AssertionError(
            f"MCP server version not set after creation for user: {user_name}"
        )
    if test_context.active_mcp_access_endpoint_id is None:
        raise AssertionError(
            f"MCP access endpoint id not set after creation for user: {user_name}"
        )

    logger.info(
        f"Successfully validated MCP server version and endpoint creation "
        f"(version: {test_context.active_mcp_server_version}, "
        f"endpoint: {test_context.active_mcp_access_endpoint_id}) in workspace '{workspace}'"
    )


def validate_mcp_access_endpoint_created(test_context: TestContext) -> None:
    """Validate that an MCP access endpoint was successfully created.

    Checks that active_mcp_access_endpoint_id is populated and no error occurred.

    Args:
        test_context: Test context containing endpoint creation results.

    Raises:
        AssertionError: If endpoint was not created or an error occurred.
    """
    validate_resource_retrieved_or_created(
        test_context=test_context,
        resource_field="active_mcp_access_endpoint_id",
        resource_type="MCP access endpoint",
        operation="creation",
    )


def validate_mcp_access_endpoint_retrieved(test_context: TestContext) -> None:
    """Validate that an MCP access endpoint was successfully retrieved.

    Checks that active_mcp_access_endpoint is populated, no error occurred, and
    the retrieved endpoint's id matches the one recorded at creation time.

    Args:
        test_context: Test context containing endpoint retrieval results.

    Raises:
        AssertionError: If retrieval failed or the retrieved endpoint doesn't match.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        raise AssertionError(
            f"MCP access endpoint retrieval failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"
        )

    retrieved = test_context.active_mcp_access_endpoint
    if retrieved is None:
        raise AssertionError(f"MCP access endpoint not set after retrieval for user: {user_name}")
    if retrieved.id != test_context.active_mcp_access_endpoint_id:
        raise AssertionError(
            f"Retrieved MCP access endpoint id '{retrieved.id}' does not match expected "
            f"'{test_context.active_mcp_access_endpoint_id}' for user: {user_name}"
        )

    logger.info(f"Successfully validated MCP access endpoint retrieval (id: {retrieved.id})")


def validate_mcp_access_endpoint_search_excludes_other_workspace(test_context: TestContext) -> None:
    """Validate that MCP access endpoint search results do not leak endpoints from other workspaces.

    Args:
        test_context: Test context containing search results and the id of an
                     access endpoint created in a different workspace.

    Raises:
        AssertionError: If search failed or the other-workspace endpoint leaked through.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        raise AssertionError(
            f"MCP access endpoint search failed for user {user_name} in workspace "
            f"'{test_context.active_workspace}': {error_response.error.code} - {error_response.error.message}"
        )

    results = test_context.mcp_access_endpoint_search_results or []
    found_ids = {endpoint.id for endpoint in results}
    if test_context.active_mcp_access_endpoint_id in found_ids:
        raise AssertionError(
            f"MCP access endpoint search in workspace '{test_context.active_workspace}' leaked endpoint "
            f"'{test_context.active_mcp_access_endpoint_id}' created in another workspace"
        )

    logger.info(
        f"Successfully validated MCP access endpoint search workspace isolation "
        f"(workspace '{test_context.active_workspace}', {len(results)} result(s), user {user_name})"
    )


def validate_mcp_access_endpoint_updated(test_context: TestContext) -> None:
    """Validate that an MCP access endpoint's URL was actually changed by the update call.

    Checking the returned object's url (rather than just "no error") matters here:
    a shallow check would still pass even if the update silently no-opped.

    Args:
        test_context: Test context containing the updated endpoint.

    Raises:
        AssertionError: If update failed or the URL wasn't actually changed.
    """
    user_name = test_context.active_user.uname if test_context.active_user else "unknown"

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        raise AssertionError(
            f"MCP access endpoint update failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"
        )

    updated = test_context.active_mcp_access_endpoint
    if updated is None:
        raise AssertionError(f"MCP access endpoint not set after update for user: {user_name}")

    # Must match the url actions.mcp_actions.action_update_mcp_access_endpoint requests.
    expected_url = "https://example.invalid/mcp-updated"
    if updated.url != expected_url:
        raise AssertionError(
            f"MCP access endpoint URL was not updated for user {user_name}: expected '{expected_url}', got '{updated.url}'"
        )

    logger.info(f"Successfully validated MCP access endpoint update (id: {updated.id}, url: {updated.url})")
