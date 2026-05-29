import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_trace_observations,
    action_create_model,
    action_log_model,
    action_end_run,
    action_write_pre_upgrade_version_configmap,
    action_ensure_upgrade_experiment,
    action_start_upgrade_run,
    action_log_upgrade_run_params,
    action_log_upgrade_run_metrics,
    action_log_upgrade_text_artifact,
    action_create_upgrade_trace,
    action_ensure_upgrade_registered_model,
    action_create_upgrade_model_version,
    action_ensure_upgrade_prompt,
    action_create_upgrade_prompt_version,
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
    validate_run_created,
    validate_run_ended,
    validate_upgrade_experiment_runs,
    validate_upgrade_prompts,
    validate_upgrade_registered_models,
    validate_upgrade_trace_sessions,
)

logger = logging.getLogger(__name__)
UPGRADE_WORKSPACE = get_upgrade_workspace()


def build_experiment_run_steps() -> list[TestStep]:
    steps = [
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
    ]
    for run_payload in EXPERIMENT_RUNS_STATE["runs"]:
        steps.extend(
            [
                TestStep(
                    action_func=make_upgrade_state_action(
                        f"action_select_{run_payload['run_name'].replace('-', '_')}",
                        current_run=run_payload,
                    )
                ),
                TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                TestStep(action_func=action_log_upgrade_run_params),
                TestStep(action_func=action_log_upgrade_run_metrics),
                TestStep(action_func=action_log_upgrade_text_artifact),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended),
            ]
        )
    steps.append(TestStep(validate_func=validate_upgrade_experiment_runs))
    return steps


def build_trace_steps() -> list[TestStep]:
    steps = [
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
    ]
    for session_payload in TRACE_STATE["sessions"]:
        for trace_payload in session_payload["traces"]:
            steps.extend(
                [
                    TestStep(
                        action_func=make_upgrade_state_action(
                            f"action_select_{trace_payload['trace_name'].replace('-', '_')}",
                            current_trace_session=session_payload,
                            current_trace=trace_payload,
                        )
                    ),
                    TestStep(action_func=action_create_upgrade_trace),
                ]
            )
    steps.append(
        TestStep(
            action_func=action_collect_upgrade_trace_observations,
            validate_func=validate_upgrade_trace_sessions,
        )
    )
    return steps


def build_registered_model_steps() -> list[TestStep]:
    steps = [
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
    ]
    for model_payload in REGISTERED_MODELS_STATE["models"]:
        steps.extend(
            [
                TestStep(
                    action_func=make_upgrade_state_action(
                        f"action_select_{model_payload['name'].replace('-', '_')}",
                        current_registered_model=model_payload,
                    )
                ),
                TestStep(action_func=action_ensure_upgrade_registered_model),
            ]
        )
        for version_payload in model_payload["versions"]:
            steps.extend(
                [
                    TestStep(
                        action_func=make_upgrade_state_action(
                            f"action_select_{version_payload['run_name'].replace('-', '_')}",
                            current_model_version=version_payload,
                            current_run={"run_name": version_payload["run_name"]},
                        )
                    ),
                    TestStep(action_func=action_start_upgrade_run, validate_func=validate_run_created),
                    TestStep(action_func=action_create_model),
                    TestStep(action_func=action_log_model),
                    TestStep(action_func=action_create_upgrade_model_version),
                    TestStep(action_func=action_end_run, validate_func=validate_run_ended),
                ]
            )
    steps.append(TestStep(validate_func=validate_upgrade_registered_models))
    return steps


def build_prompt_steps() -> list[TestStep]:
    steps = [
        TestStep(
            action_func=make_upgrade_state_action(
                "action_select_prompts_state",
                case=PROMPTS_STATE,
            )
        ),
        TestStep(
            action_func=action_write_pre_upgrade_version_configmap,
            validate_func=validate_pre_upgrade_version_configmap,
        ),
    ]
    for prompt_payload in PROMPTS_STATE["prompts"]:
        steps.extend(
            [
                TestStep(
                    action_func=make_upgrade_state_action(
                        f"action_select_{prompt_payload['name'].replace('-', '_')}",
                        current_prompt=prompt_payload,
                    )
                ),
                TestStep(action_func=action_ensure_upgrade_prompt),
            ]
        )
        for index, version_payload in enumerate(prompt_payload["versions"], start=1):
            steps.extend(
                [
                    TestStep(
                        action_func=make_upgrade_state_action(
                            f"action_select_{prompt_payload['name'].replace('-', '_')}_version_{index}",
                            current_prompt_version=version_payload,
                        )
                    ),
                    TestStep(action_func=action_create_upgrade_prompt_version),
                ]
            )
    steps.append(TestStep(validate_func=validate_upgrade_prompts))
    return steps


@pytest.mark.pre_upgrade
class TestMLflow310PreUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Seed static experiment runs",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=build_experiment_run_steps(),
        ),
        TestData(
            test_name="Seed static trace sessions",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=build_trace_steps(),
        ),
        TestData(
            test_name="Seed static registered models",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=build_registered_model_steps(),
        ),
        TestData(
            test_name="Seed static prompts",
            workspace_to_use=UPGRADE_WORKSPACE,
            test_steps=build_prompt_steps(),
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
