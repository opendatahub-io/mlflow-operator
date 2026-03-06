import logging
import random
import string
from typing import ClassVar

import pytest

from .actions import (
    action_list_workspaces,
)
from .base import TestBase
from .shared import TestData, TestStep
from .validations import validate_workspaces_filtered

logger = logging.getLogger(__name__)
random_gen = random.Random()


@pytest.mark.Workspaces
class TestWorkspaces(TestBase):
    """Test workspace discovery and filtering."""

    @pytest.fixture(autouse=True)
    def create_unlabeled_namespace(self):
        """Create an unlabeled namespace for workspace filter validation."""
        random_suffix = "".join(random_gen.choices(string.ascii_lowercase + string.digits, k=8))
        namespace = f"unlabeled-workspace-{random_suffix}"

        logger.info(f"Creating unlabeled namespace: {namespace}")
        self.k8_manager.create_namespace(namespace)
        self.test_context.unlabeled_namespace = namespace
        self.test_context.add_namespace_for_cleanup(namespace)

        yield

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Unlabeled namespace is filtered when workspaceLabelSelector is enabled",
            test_steps=[
                TestStep(action_func=action_list_workspaces, validate_func=validate_workspaces_filtered),
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

