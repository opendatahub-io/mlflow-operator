"""Skill Registry upgrade coverage.

No upstream MLflow release includes the Skill Registry yet. OpenDataHub ships it in the 3.17
MLflow image (see config/component_metadata.yaml), so this file is gated at 3.17 and only
seeds a source deployment that has the registry.
"""

import logging
from typing import ClassVar

import mlflow
import pytest

from ..actions import (
    make_upgrade_state_action,
    action_collect_upgrade_oci_skill_versions,
    action_collect_upgrade_skill_observations,
    action_register_upgrade_oci_skill_version,
    action_register_upgrade_skill_versions,
    action_tag_and_alias_upgrade_skill,
)
from ...constants.config import Config
from ...shared import TestData, TestStep
from ...validations import validate_no_error
from ..shared.upgrade_state_3_17 import SKILL_REGISTRY_OCI_STATE, SKILL_REGISTRY_STATE
from ..phase_base import UpgradePhaseBase
from ..utils import get_upgrade_test_workspace
from ..validations import (
    validate_registered_upgrade_skill_versions,
    validate_upgrade_oci_skill_version,
    validate_upgrade_skill_registry,
)

logger = logging.getLogger(__name__)
UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


@pytest.mark.pre_upgrade
class TestMLflow317PreUpgrade(UpgradePhaseBase):
    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name="Seed static OCI Skill",
            workspace_to_use=UPGRADE_TEST_WORKSPACE,
            test_steps=[
                TestStep(
                    action_func=make_upgrade_state_action(
                        "action_select_skill_registry_oci_state",
                        skill_registry_oci=SKILL_REGISTRY_OCI_STATE,
                    )
                ),
                TestStep(
                    action_func=action_register_upgrade_oci_skill_version,
                    validate_func=validate_no_error,
                ),
                TestStep(
                    action_func=action_collect_upgrade_oci_skill_versions,
                    validate_func=validate_upgrade_oci_skill_version,
                ),
            ],
        ),
    ]

    # Registering Skill versions from a local directory needs MLflow artifact serving, so the
    # Skill is only seeded where the Skill Registry tests also register local Skills.
    if Config.effective_serve_artifacts():
        test_scenarios.append(
            TestData(
                test_name="Seed static Skill",
                workspace_to_use=UPGRADE_TEST_WORKSPACE,
                test_steps=[
                    TestStep(
                        action_func=make_upgrade_state_action(
                            "action_select_skill_registry_state",
                            skill_registry=SKILL_REGISTRY_STATE,
                        )
                    ),
                    TestStep(
                        action_func=action_register_upgrade_skill_versions,
                        validate_func=validate_registered_upgrade_skill_versions,
                    ),
                    TestStep(
                        action_func=action_tag_and_alias_upgrade_skill,
                        validate_func=validate_no_error,
                    ),
                    TestStep(
                        action_func=action_collect_upgrade_skill_observations,
                        validate_func=validate_upgrade_skill_registry,
                    ),
                ],
            )
        )

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
