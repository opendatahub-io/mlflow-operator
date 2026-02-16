"""Shared validation utilities for MLflow RBAC tests.

This module contains common validation functions that can be reused across
different resource types (experiments, registered models, etc.).
"""

import logging
from typing import Optional
from ..shared import TestContext, ErrorResponse, ErrorCode

logger = logging.getLogger(__name__)


def validate_authentication_denied(test_context: TestContext) -> None:
    """Validate that an action failed due to authentication denial.

    Checks that an authentication or authorization error occurred during the action.

    Args:
        test_context: Test context containing structured error information.

    Raises:
        AssertionError: If no error occurred or error is not permission-related.
    """
    user_name = test_context.active_user.uname
    workspace = test_context.active_workspace
    logger.info(f"Validating that action failed as expected for user '{user_name}' in workspace '{workspace}'")

    # Validate that an error occurred
    if test_context.last_error is None:
        logger.error(f"Validation failed: Action should have failed for unauthorized user '{user_name}', but succeeded")
        raise AssertionError(f"Action should have failed for unauthorized user '{user_name}', but succeeded")

    error_response: ErrorResponse = test_context.last_error

    # Check if it's a permission or authentication error
    if not error_response.is_permission_error():
        logger.error(f"Validation failed: Expected permission/authentication error, got: {error_response.error.code} - {error_response.error.message}")
        raise AssertionError(
            f"Action failed with unexpected error for user {user_name}: {error_response.error.code} - {error_response.error.message}. "
            f"Expected permission/authentication error."
        )

    # Log specific type of permission error for debugging
    error_code = error_response.error.code
    if error_code == ErrorCode.PERMISSION_DENIED:
        logger.info("Successfully validated action failure - user lacks required permissions (RBAC working correctly)")
    elif error_code in [ErrorCode.UNAUTHENTICATED, ErrorCode.AUTHENTICATION_FAILED]:
        logger.info(f"Successfully validated action failure - authentication failed ({error_code})")
    elif error_code == ErrorCode.FORBIDDEN:
        logger.info("Successfully validated action failure - access forbidden")
    elif error_code == ErrorCode.WORKSPACE_ACCESS_DENIED:
        logger.info("Successfully validated action failure - workspace access denied")
    else:
        logger.info(f"Successfully validated action failure - permission error ({error_code})")

    logger.debug(f"Error details: {error_response.error.message}")
    if error_response.error.details:
        logger.debug(f"Error context: {error_response.error.details}")


def validate_resource_retrieved_or_created(
    test_context: TestContext,
    resource_field: str,
    resource_type: str,
    operation: str
) -> None:
    """Generic validation for resource retrieval or creation operations.

    Validates that no error occurred and the specified resource field is set.

    Args:
        test_context: Test context containing operation results
        resource_field: Name of TestContext field containing resource identifier
        resource_type: Type of resource (for logging)
        operation: Operation performed (for logging)

    Raises:
        AssertionError: If operation failed or resource identifier not set
    """
    user_name = test_context.active_user.uname
    workspace = test_context.active_workspace

    logger.info(f"Validating {resource_type} {operation} for user '{user_name}' in workspace '{workspace}'")

    # Validate no error occurred
    if test_context.last_error is not None:
        error_response: ErrorResponse = test_context.last_error
        logger.error(f"Validation failed: {resource_type} {operation} encountered an error for user '{user_name}': {error_response.error.code} - {error_response.error.message}")
        raise AssertionError(
            f"{resource_type} {operation} failed for user {user_name}: {error_response.error.code} - {error_response.error.message}"
        )
    logger.debug(f"No errors detected during {resource_type} {operation}")

    # Validate resource identifier is set
    resource_value = getattr(test_context, resource_field, None)
    if resource_value is None:
        logger.error(f"Validation failed: {resource_type} identifier not set after {operation} for user '{user_name}'")
        raise AssertionError(
            f"{resource_type} identifier not set after {operation} for user: {user_name}"
        )

    logger.info(f"Successfully validated {resource_type} {operation} ({resource_field}: {resource_value})")