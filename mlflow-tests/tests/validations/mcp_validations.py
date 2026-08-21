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
    logger.info(f"Validating MCP server deletion for user '{test_context.active_user.uname}' in workspace '{test_context.active_workspace}'")

    # Validate no error occurred
    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        logger.error(f"Validation failed: MCP server deletion encountered an error for user '{test_context.active_user.uname}': {error_response.error.code} - {error_response.error.message}")
        assert False, \
            f"MCP server deletion failed for user {test_context.active_user.uname}: {error_response.error.code} - {error_response.error.message}"
    logger.debug("No errors detected during MCP server deletion")

    # Validate server name is set
    if test_context.active_mcp_server_name is None:
        logger.error(f"Validation failed: Server name not set after deletion for user '{test_context.active_user.uname}'")
    assert test_context.active_mcp_server_name is not None, \
        f"MCP server name not set after deletion for user: {test_context.active_user.uname}"
    logger.debug(f"Verifying deletion status for MCP server {test_context.active_mcp_server_name}")

    # Verify server no longer exists
    deletion_verified = False
    try:
        mlflow.genai.get_mcp_server(test_context.active_mcp_server_name)
        # If we get here without exception, the server still exists (unexpected)
        logger.error(f"Validation failed: MCP server {test_context.active_mcp_server_name} still exists after deletion")
    except MlflowException as e:
        # Expected: Server should not be found
        error_message = str(e)
        if "RESOURCE_DOES_NOT_EXIST" in error_message or "does not exist" in error_message.lower():
            deletion_verified = True
            logger.debug("MCP server deletion verified - server not found as expected")
        else:
            logger.error(f"Validation failed: Unexpected MLflow error during deletion verification: {error_message}")
    except Exception as e:
        logger.error(f"Validation failed: Unexpected error during deletion verification: {e}")

    assert deletion_verified, \
        f"MCP server deletion verification failed - server {test_context.active_mcp_server_name} still exists " \
        f"for user: {test_context.active_user.uname}"

    logger.info(f"Successfully validated MCP server deletion (name: {test_context.active_mcp_server_name})")


def validate_mcp_server_version_and_endpoint_created(test_context: TestContext) -> None:
    """Validate that an MCP server version and access endpoint were successfully created.

    Checks that active_mcp_server_version and active_mcp_access_endpoint_id are
    populated and no error occurred.

    Args:
        test_context: Test context containing version/endpoint creation results.

    Raises:
        AssertionError: If creation failed or identifiers were not set.
    """
    user_name = test_context.active_user.uname
    workspace = test_context.active_workspace

    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        assert False, \
            f"MCP server version/endpoint creation failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"

    assert test_context.active_mcp_server_version is not None, \
        f"MCP server version not set after creation for user: {user_name}"
    assert test_context.active_mcp_access_endpoint_id is not None, \
        f"MCP access endpoint id not set after creation for user: {user_name}"

    logger.info(
        f"Successfully validated MCP server version and endpoint creation "
        f"(version: {test_context.active_mcp_server_version}, "
        f"endpoint: {test_context.active_mcp_access_endpoint_id}) in workspace '{workspace}'"
    )
