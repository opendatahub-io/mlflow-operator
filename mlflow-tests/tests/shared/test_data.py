from dataclasses import dataclass
from .user_info import UserInfo
from .test_context import TestContext
from typing import Callable, Optional


@dataclass
class TestStep:
    """
    Encapsulates the test step that a user indent to take in the test
    """

    validate_func: Callable[[TestContext], None] = None
    action_func: Callable[[TestContext], None] = None
    workspace_to_use: str | None = None
    user_info: UserInfo | None = None
    # Prevent pytest from collecting this dataclass as a test class because its
    # name starts with "Test".
    __test__ = False

    def __str__(self) -> str:
        validate_name = self.validate_func.__name__ if self.validate_func else None
        action_name = self.action_func.__name__ if self.action_func else None
        return (f"Test Data: "
                f"workspace={self.workspace_to_use} "
                f"action_func={action_name} "
                f"validate_func={validate_name} "
                f"user={self.user_info}")

    def __repr__(self) -> str:
        return self.__str__()


@dataclass
class TestData:
    """Test data structure for parameterized tests.

    Encapsulates test configuration including user permissions, workspace,
    action to perform, and validation to execute.
    """

    test_name: str
    test_steps: list[TestStep] | TestStep
    user_info: Optional[UserInfo] = None
    workspace_to_use: Optional[str] = None
    resource_slot: Optional[str] = None
    # Prevent pytest from collecting this dataclass as a test class because its
    # name starts with "Test".
    __test__ = False

    def __str__(self) -> str:
        user = self.user_info.__str__() if self.user_info else None
        return (f"Test Data: "
                f"name={self.test_name} "
                f"user_info={user} "
                f"workspace={self.workspace_to_use} "
                f"resource_slot={self.resource_slot}")

    def __repr__(self) -> str:
        return self.__str__()