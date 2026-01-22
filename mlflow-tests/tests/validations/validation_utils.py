"""Shared validation utilities for MLflow RBAC tests.

This module contains common validation functions that can be reused across
different resource types (experiments, registered models, etc.).
"""

import logging
from typing import Optional
from ..shared import TestContext

logger = logging.getLogger(__name__)


def validate_action_failed(test_context: TestContext) -> None:
    """Validate that an action failed as expected.

    Checks that an authentication error occurred during the action.

    Args:
        test_context: Test context containing error information.

    Raises:
        AssertionError: If no error occurred or error message is unexpected.
    """
    logger.info(f"Validating that action failed as expected for user '{test_context.active_user.uname}' in workspace '{test_context.active_workspace}'")

    # Validate that an error occurred
    if test_context.last_error is None:
        logger.error(f"Validation failed: Action should have failed for unauthorized user '{test_context.active_user.uname}', but succeeded")
    assert test_context.last_error is not None, \
        f"Action should have failed for unauthorized user: {test_context.active_user.uname}, " \
        f"but succeeded"
    logger.debug(f"Confirmed error occurred: {test_context.last_error}")

    # Validate error message contains authentication or permission failure
    error_message = str(test_context.last_error)
    expected_errors = [
        "Authentication with the Kubernetes API failed",  # Authentication failure
        "PERMISSION_DENIED",  # Permission denied (RBAC working correctly)
        "UNAUTHENTICATED",    # Another form of authentication failure
        "Forbidden",          # HTTP 403 permission denied
    ]

    error_found = any(expected_error in error_message for expected_error in expected_errors)
    if not error_found:
        logger.error(f"Validation failed: Action failed with unexpected error - expected authentication or permission failure, got: {error_message}")
    assert error_found, \
        f"Action failed with unexpected error for user {test_context.active_user.uname}: {error_message}. " \
        f"Expected one of: {expected_errors}"

    # Log which type of error occurred for debugging
    if "PERMISSION_DENIED" in error_message:
        logger.info(f"Successfully validated action failure - user lacks required permissions (RBAC working correctly)")
    elif "Authentication" in error_message or "UNAUTHENTICATED" in error_message:
        logger.info(f"Successfully validated action failure - authentication failed")
    else:
        logger.info(f"Successfully validated action failure - access denied")


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
        logger.error(f"Validation failed: {resource_type} {operation} encountered an error for user '{user_name}': {test_context.last_error}")
    assert test_context.last_error is None, \
        f"{resource_type} {operation} failed for user {user_name}: {test_context.last_error}"
    logger.debug(f"No errors detected during {resource_type} {operation}")

    # Validate resource identifier is set
    resource_value = getattr(test_context, resource_field, None)
    if resource_value is None:
        logger.error(f"Validation failed: {resource_type} identifier not set after {operation} for user '{user_name}'")
    assert resource_value is not None, \
        f"{resource_type} identifier not set after {operation} for user: {user_name}"

    logger.info(f"Successfully validated {resource_type} {operation} ({resource_field}: {resource_value})")