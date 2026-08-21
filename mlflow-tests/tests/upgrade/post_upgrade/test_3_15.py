import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import make_upgrade_state_action
from ...shared import TestData, TestStep
from ..shared.upgrade_state_3_15 import MCP_SERVER_STATE
from ..phase_base import UpgradePhaseBase
from ..utils import get_upgrade_test_workspace
from ..validations import validate_upgrade_mcp_servers

logger = logging.getLogger(__name__)
UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


@pytest.mark.post_upgrade
class TestMLflow315PostUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate static MCP server",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_mcp_server_state",
                        case=MCP_SERVER_STATE,
                    )
                ),
                TestStep(validate_func=validate_upgrade_mcp_servers),
            ],
        ),
    ]

    @pytest.mark.parametrize("test_data", test_scenarios, ids=lambda x: x.test_name)
    def test_post_upgrade_scenario(self, test_data: TestData) -> None:
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        self.reset_upgrade_state()

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")

        self._execute_test_steps(test_data=test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")
