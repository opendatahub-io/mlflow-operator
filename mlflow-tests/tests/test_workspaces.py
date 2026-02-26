import logging
from typing import ClassVar

import pytest

from .actions import (
    action_create_unlabeled_namespace,
    action_list_workspaces,
    action_delete_unlabeled_namespace,
)
from .base import TestBase
from .shared import TestData, TestStep
from .validations import validate_workspaces_filtered

logger = logging.getLogger(__name__)


@pytest.mark.Workspaces
class TestWorkspaces(TestBase):
    """Test workspace discovery and filtering."""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Unlabeled namespace is filtered when workspaceLabelSelector is enabled",
            test_steps=[
                TestStep(action_func=action_create_unlabeled_namespace),
                TestStep(action_func=action_list_workspaces, validate_func=validate_workspaces_filtered),
                TestStep(action_func=action_delete_unlabeled_namespace),
            ],
        )
    ]

    @pytest.mark.parametrize("test_data", test_scenarios, ids=lambda x: x.test_name)
    def test_workspaces(self, test_data: TestData):
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info("=" * 80)

        self._execute_test_steps(test_data=test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")

