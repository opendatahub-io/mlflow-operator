import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_skill_observations,
    action_collect_upgrade_trace_observations,
    action_delete_upgrade_skill,
    action_read_pre_upgrade_version_configmap,
    action_register_upgrade_skill_versions,
    action_require_skill_registry,
    action_tag_and_alias_upgrade_skill,
)
from ...shared import TestData, TestStep
from ...validations import validate_no_error
from ..shared.upgrade_state_3_10 import (
    EXPERIMENT_RUNS_STATE,
    REGISTERED_MODELS_STATE,
    SKILL_REGISTRY_STATE,
    TRACE_STATE,
)
from ..phase_base import UpgradePhaseBase
from ..utils import get_upgrade_test_workspace
from ..validations import (
    validate_pre_upgrade_version_configmap,
    validate_upgrade_experiment_runs,
    validate_upgrade_registered_models,
    validate_upgrade_skill_deleted,
    validate_upgrade_skill_registry,
    validate_upgrade_trace_sessions,
)

logger = logging.getLogger(__name__)
UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


@pytest.mark.post_upgrade
class TestMLflow310PostUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Validate static experiment runs",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_experiment_runs_state",
                        case=EXPERIMENT_RUNS_STATE,
                    )
                ),
                TestStep(
                    action_func=action_read_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(validate_func=validate_upgrade_experiment_runs),
            ],
        ),
        TestData(
            test_name="Validate static trace sessions",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_trace_state",
                        case=TRACE_STATE,
                    )
                ),
                TestStep(
                    action_func=action_read_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(
                    action_func=action_collect_upgrade_trace_observations,
                    validate_func=validate_upgrade_trace_sessions,
                ),
            ],
        ),
        TestData(
            test_name="Validate static registered models",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_registered_models_state",
                        case=REGISTERED_MODELS_STATE,
                    )
                ),
                TestStep(
                    action_func=action_read_pre_upgrade_version_configmap,
                    validate_func=validate_pre_upgrade_version_configmap,
                ),
                TestStep(validate_func=validate_upgrade_registered_models),
            ],
        ),
        # This lives in the 3.10 module because post-upgrade files are selected by the
        # pre-upgrade version: the seeded job upgrades from 3.10, which has no Skill Registry,
        # so this proves the registry works on a database migrated from a pre-registry version.
        TestData(
            test_name="Validate Skill Registry on a migrated database",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_skill_registry_state",
                        skill_registry=SKILL_REGISTRY_STATE,
                    )
                ),
                TestStep(
                    action_func=action_require_skill_registry,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_register_upgrade_skill_versions,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_tag_and_alias_upgrade_skill,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_collect_upgrade_skill_observations,
                    validate_func=validate_upgrade_skill_registry,
                ),
                TestStep(
                    action_func=action_delete_upgrade_skill,
                    validate_func=validate_upgrade_skill_deleted,
                ),
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
