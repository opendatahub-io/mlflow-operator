import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_trace_observations,
    action_write_pre_upgrade_version_configmap,
    action_ensure_upgrade_experiment,
    action_create_upgrade_trace,
)
from ...shared import TestData, TestStep
from ..shared.upgrade_state_3_11 import TRACE_STATE
from ..phase_base import UpgradePhaseBase
from ..utils import get_upgrade_test_workspace
from ..validations import (
    validate_pre_upgrade_version_configmap,
    validate_upgrade_trace_sessions,
)

logger = logging.getLogger(__name__)
UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


@pytest.mark.pre_upgrade
class TestMLflow311PreUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Seed static trace attachments",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_trace_state",
                        case=TRACE_STATE,
                        current_experiment=TRACE_STATE,
                    )
                ),
                TestStep(
                    action_func=action_write_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(action_func=action_ensure_upgrade_experiment),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_trace_1_1",
                        current_trace_session=TRACE_STATE["sessions"][0],
                        current_trace=TRACE_STATE["sessions"][0]["traces"][0],
                    )
                ),
                TestStep(action_func=action_create_upgrade_trace),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_trace_1_2",
                        current_trace_session=TRACE_STATE["sessions"][0],
                        current_trace=TRACE_STATE["sessions"][0]["traces"][1],
                    )
                ),
                TestStep(action_func=action_create_upgrade_trace),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_trace_2_1",
                        current_trace_session=TRACE_STATE["sessions"][1],
                        current_trace=TRACE_STATE["sessions"][1]["traces"][0],
                    )
                ),
                TestStep(action_func=action_create_upgrade_trace),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_trace_2_2",
                        current_trace_session=TRACE_STATE["sessions"][1],
                        current_trace=TRACE_STATE["sessions"][1]["traces"][1],
                    )
                ),
                TestStep(action_func=action_create_upgrade_trace),
                TestStep(
                    action_func=action_collect_upgrade_trace_observations,
                    validate_func=validate_upgrade_trace_sessions,
                ),
            ],
        ),
    ]

    @pytest.mark.parametrize("test_data", test_scenarios, ids=lambda x: x.test_name)
    def test_pre_upgrade_scenario(self, test_data: TestData) -> None:
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
