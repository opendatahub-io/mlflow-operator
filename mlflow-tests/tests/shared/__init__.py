"""Shared test objects package.

Contains shared test objects and data structures used across multiple tests.
"""

from .test_context import TestContext
from .test_data import TestData, TestStep
from .user_info import UserInfo
from .error_models import ErrorResponse, Error, ErrorCode
from .resource_map import (
    PRIMARY_RESOURCE_REF,
    PRIMARY_RESOURCE_SLOT,
    SECONDARY_RESOURCE_REF,
    SECONDARY_RESOURCE_SLOT,
    get_resource_entry,
    resolve_resource_name_refs,
)

__all__ = [
    "Error",
    "ErrorCode",
    "ErrorResponse",
    "PRIMARY_RESOURCE_REF",
    "PRIMARY_RESOURCE_SLOT",
    "SECONDARY_RESOURCE_REF",
    "SECONDARY_RESOURCE_SLOT",
    "TestContext",
    "TestData",
    "TestStep",
    "UserInfo",
    "get_resource_entry",
    "resolve_resource_name_refs",
]
