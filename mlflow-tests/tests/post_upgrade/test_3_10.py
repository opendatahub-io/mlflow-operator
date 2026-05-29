import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_trace_observations,
    action_read_pre_upgrade_version_configmap,
)
from ..shared import TestData, TestStep
from ..shared.upgrade_state_3_10 import (
    EXPERIMENT_RUNS_STATE,
    PROMPTS_STATE,
    REGISTERED_MODELS_STATE,
    TRACE_STATE,
)
from ..upgrade_phase_base import UpgradePhaseBase
from ..upgrade_utils import get_upgrade_workspace
from ..validations import (
    validate_pre_upgrade_version_configmap,
    validate_upgrade_experiment_runs,
    validate_upgrade_prompts,
    validate_upgrade_registered_models,
    validate_upgrade_trace_sessions,
)

logger = logging.getLogger(__name__)
UPGRADE_WORKSPACE = get_upgrade_workspace()


def build_post_upgrade_steps(validate_func, action_func=None) -> list[TestStep]:
    steps = [
        TestStep(
            action_func=action_read_pre_upgrade_version_configmap,
            validate_func=validate_pre_upgrade_version_configmap,
        ),
    ]
    if action_func is not None:
        steps.append(TestStep(action_func=action_func, validate_func=validate_func))
    else:
        steps.append(TestStep(validate_func=validate_func))
    return steps


@pytest.mark.post_upgrade
class TestMLflow310PostUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate static experiment runs",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_experiment_runs_state",
                        case=EXPERIMENT_RUNS_STATE,
                    )
                ),
                *build_post_upgrade_steps(validate_upgrade_experiment_runs),
            ],
        ),
        TestData(
            test_name="Validate static trace sessions",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_trace_state",
                        case=TRACE_STATE,
                    )
                ),
                *build_post_upgrade_steps(
                    validate_upgrade_trace_sessions,
                    action_collect_upgrade_trace_observations,
                ),
            ],
        ),
        TestData(
            test_name="Validate static registered models",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_registered_models_state",
                        case=REGISTERED_MODELS_STATE,
                    )
                ),
                *build_post_upgrade_steps(validate_upgrade_registered_models),
            ],
        ),
        TestData(
            test_name="Validate static prompts",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_prompts_state",
                        case=PROMPTS_STATE,
                    )
                ),
                *build_post_upgrade_steps(validate_upgrade_prompts),
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
