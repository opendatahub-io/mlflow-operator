"""Pydantic models for error handling in MLflow tests.

This module defines structured error models that replace raw Exception objects
with type-safe, structured error information.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from enum import Enum


class ErrorCode(str, Enum):
    """Standard error codes for MLflow test operations."""
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    WORKSPACE_ACCESS_DENIED = "WORKSPACE_ACCESS_DENIED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


class Error(BaseModel):
    """Error details model."""
    code: ErrorCode = Field(..., description="Error code indicating the type of error")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[str] = Field(None, description="Additional error details or context")

    model_config = ConfigDict(use_enum_values=True)


class ErrorResponse(BaseModel):
    """Error response model matching the MLflow API error structure.

    Example:
        {
            'error': {
                'code': 'PERMISSION_DENIED',
                'message': 'Permission denied for requested operation.',
                'details': 'User lacks CREATE permission on workspace test-workspace'
            }
        }
    """
    error: Error = Field(..., description="Error information")

    @classmethod
    def from_exception(cls, exception: Exception, workspace: Optional[str] = None,
                      user: Optional[str] = None) -> "ErrorResponse":
        """Create ErrorResponse from a Python exception.

        Args:
            exception: The original exception
            workspace: Current workspace context (optional)
            user: Current user context (optional)

        Returns:
            ErrorResponse: Structured error response
        """
        error_message = str(exception)
        error_code = cls._classify_error(error_message)

        # Add context to error message if available
        details = None
        if workspace or user:
            context_parts = []
            if user:
                context_parts.append(f"User: {user}")
            if workspace:
                context_parts.append(f"Workspace: {workspace}")
            details = f"Context: {', '.join(context_parts)}"

        return cls(
            error=Error(
                code=error_code,
                message=error_message,
                details=details
            )
        )

    @staticmethod
    def _classify_error(error_message: str) -> ErrorCode:
        """Classify error message into appropriate error code.

        Args:
            error_message: The error message to classify

        Returns:
            ErrorCode: Appropriate error code based on message content
        """
        error_lower = error_message.lower()

        # Permission and authentication errors
        if "permission" in error_lower and "denied" in error_lower:
            return ErrorCode.PERMISSION_DENIED
        if "unauthenticated" in error_lower:
            return ErrorCode.UNAUTHENTICATED
        if "forbidden" in error_lower:
            return ErrorCode.FORBIDDEN
        if "authentication" in error_lower and "failed" in error_lower:
            return ErrorCode.AUTHENTICATION_FAILED

        # Resource errors
        if "not found" in error_lower or "does not exist" in error_lower:
            return ErrorCode.RESOURCE_NOT_FOUND
        if "already exists" in error_lower or "duplicate" in error_lower:
            return ErrorCode.RESOURCE_ALREADY_EXISTS

        # Workspace errors
        if "workspace" in error_lower and ("access" in error_lower or "denied" in error_lower):
            return ErrorCode.WORKSPACE_ACCESS_DENIED

        # Quota errors
        if "quota" in error_lower or "limit" in error_lower:
            return ErrorCode.QUOTA_EXCEEDED

        # Request errors
        if "invalid" in error_lower or "bad request" in error_lower:
            return ErrorCode.INVALID_REQUEST

        # Default to internal error
        return ErrorCode.INTERNAL_ERROR

    def is_permission_error(self) -> bool:
        """Check if this is a permission-related error.

        Returns:
            bool: True if error is permission-related
        """
        return self.error.code in [
            ErrorCode.PERMISSION_DENIED,
            ErrorCode.UNAUTHENTICATED,
            ErrorCode.FORBIDDEN,
            ErrorCode.AUTHENTICATION_FAILED,
            ErrorCode.WORKSPACE_ACCESS_DENIED
        ]

    def is_authentication_error(self) -> bool:
        """Check if this is an authentication-related error.

        Returns:
            bool: True if error is authentication-related
        """
        return self.error.code in [
            ErrorCode.UNAUTHENTICATED,
            ErrorCode.AUTHENTICATION_FAILED,
            ErrorCode.FORBIDDEN
        ]