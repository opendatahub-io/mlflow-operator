from dataclasses import dataclass
from .user_info import UserInfo
from .test_context import TestContext
from typing import Callable


@dataclass
class TestData:
    """Test data structure for parameterized tests.

    Encapsulates test configuration including user permissions, workspace,
    action to perform, and validation to execute.
    """

    test_name: str
    user_info: UserInfo
    workspace_to_use: str
    validate_func: list[Callable[[TestContext], None]] | Callable[[TestContext], None]
    action_func: list[Callable[[TestContext], None]] | Callable[[TestContext], None] | None = None

    def __str__(self) -> str:
        return (f"Test Data: "
                f"name={self.test_name} "
                f"user_info={self.user_info.__str__()} ")

    def __repr__(self) -> str:
        return self.__str__()