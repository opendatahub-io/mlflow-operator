import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_trace_observations,
    action_write_pre_upgrade_version_configmap,
    action_ensure_upgrade_experiment,
    action_start_upgrade_run,
    action_log_upgrade_run_params,
    action_log_upgrade_run_metrics,
    action_log_upgrade_text_artifact,
    action_create_upgrade_trace,
    action_ensure_upgrade_registered_model,
    action_create_upgrade_model_version,
)
from ...actions import action_create_model, action_log_model, action_end_run
from ...shared import TestData, TestStep
from ..shared.upgrade_state_3_10 import (
    EXPERIMENT_RUNS_STATE,
    REGISTERED_MODELS_STATE,
    TRACE_STATE,
)
from ..phase_base import UpgradePhaseBase
from ..utils import get_upgrade_test_workspace
from ..validations import (
    validate_pre_upgrade_version_configmap,
    validate_upgrade_experiment_runs,
    validate_upgrade_registered_models,
    validate_upgrade_trace_sessions,
)
from ...validations import validate_run_created, validate_run_ended

logger = logging.getLogger(__name__)
UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


@pytest.mark.pre_upgrade
class TestMLflow310PreUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Seed static experiment runs",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_experiment_runs_state",
                        case=EXPERIMENT_RUNS_STATE,
                        current_experiment=EXPERIMENT_RUNS_STATE,
                    )
                ),
                TestStep(
                    action_func=action_write_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(action_func=action_ensure_upgrade_experiment),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_run_static_1",
                        current_run=EXPERIMENT_RUNS_STATE["runs"][0],
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_log_upgrade_run_params),
                TestStep(action_func=action_log_upgrade_run_metrics),
                TestStep(action_func=action_log_upgrade_text_artifact),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_run_static_2",
                        current_run=EXPERIMENT_RUNS_STATE["runs"][1],
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_log_upgrade_run_params),
                TestStep(action_func=action_log_upgrade_run_metrics),
                TestStep(action_func=action_log_upgrade_text_artifact),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(validate_func=validate_upgrade_experiment_runs),
            ],
        ),
        TestData(
            test_name="Seed static trace sessions",
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
                        "action_select_upgrade_trace_1_3",
                        current_trace_session=TRACE_STATE["sessions"][0],
                        current_trace=TRACE_STATE["sessions"][0]["traces"][2],
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
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_trace_2_3",
                        current_trace_session=TRACE_STATE["sessions"][1],
                        current_trace=TRACE_STATE["sessions"][1]["traces"][2],
                    )
                ),
                TestStep(action_func=action_create_upgrade_trace),
                TestStep(
                    action_func=action_collect_upgrade_trace_observations,
                    validate_func=validate_upgrade_trace_sessions,
                ),
            ],
        ),
        TestData(
            test_name="Seed static registered models",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_registered_models_state",
                        case=REGISTERED_MODELS_STATE,
                        current_experiment=REGISTERED_MODELS_STATE,
                    )
                ),
                TestStep(
                    action_func=action_write_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(action_func=action_ensure_upgrade_experiment),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_registered_model_1",
                        current_registered_model=REGISTERED_MODELS_STATE["models"][0],
                    )
                ),
                TestStep(action_func=action_ensure_upgrade_registered_model),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_model_1_run_1",
                        current_model_version=REGISTERED_MODELS_STATE["models"][0]["versions"][0],
                        current_run={"run_name": REGISTERED_MODELS_STATE["models"][0]["versions"][0]["run_name"]},
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model),
                TestStep(action_func=action_log_model),
                TestStep(action_func=action_create_upgrade_model_version),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_model_1_run_2",
                        current_model_version=REGISTERED_MODELS_STATE["models"][0]["versions"][1],
                        current_run={"run_name": REGISTERED_MODELS_STATE["models"][0]["versions"][1]["run_name"]},
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model),
                TestStep(action_func=action_log_model),
                TestStep(action_func=action_create_upgrade_model_version),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_registered_model_2",
                        current_registered_model=REGISTERED_MODELS_STATE["models"][1],
                    )
                ),
                TestStep(action_func=action_ensure_upgrade_registered_model),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_model_2_run_1",
                        current_model_version=REGISTERED_MODELS_STATE["models"][1]["versions"][0],
                        current_run={"run_name": REGISTERED_MODELS_STATE["models"][1]["versions"][0]["run_name"]},
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model),
                TestStep(action_func=action_log_model),
                TestStep(action_func=action_create_upgrade_model_version),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_upgrade_model_2_run_2",
                        current_model_version=REGISTERED_MODELS_STATE["models"][1]["versions"][1],
                        current_run={"run_name": REGISTERED_MODELS_STATE["models"][1]["versions"][1]["run_name"]},
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model),
                TestStep(action_func=action_log_model),
                TestStep(action_func=action_create_upgrade_model_version),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                TestStep(validate_func=validate_upgrade_registered_models),
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
