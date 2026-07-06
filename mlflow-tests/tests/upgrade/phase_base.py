"""Shared base helpers for upgrade-phase pytest modules."""

from __future__ import annotations

import mlflow
import pytest

from ..base import TestBase
from .utils import get_upgrade_test_workspace


UPGRADE_TEST_WORKSPACE = get_upgrade_test_workspace()


class UpgradePhaseBase(TestBase):
    """Common setup for explicit pre/post-upgrade pytest modules."""

    @pytest.fixture(autouse=True)
    def configure_upgrade_admin_context(self, init_clients, admin_user_context):
        self.test_context.user_client = self.admin_client
        self.test_context.workspaces = [UPGRADE_TEST_WORKSPACE]
        self.test_context.active_workspace = UPGRADE_TEST_WORKSPACE
        mlflow.set_workspace(self.test_context.active_workspace)

    def reset_upgrade_state(self) -> None:
        """Reset the mutable upgrade state before running an upgrade scenario."""
        self.test_context.upgrade_state = {}
        self.test_context.upgrade_observed_state = {}
        self.test_context.active_experiment_id = None
        self.test_context.active_model_name = None
        self.test_context.current_run_id = None
        self.test_context.current_trace_id = None
        self.test_context.current_trace_name = None
        self.test_context.temp_artifact_path = None
        self.test_context.temp_artifact_content = None
        self.test_context.artifact_list = None
        self.test_context.downloaded_path = None
        self.test_context.model = None
        self.test_context.model_uri = None
        self.test_context.active_trace = None
        self.test_context.artifact_location = None
        self.test_context.active_workspace = UPGRADE_TEST_WORKSPACE
        mlflow.set_workspace(self.test_context.active_workspace)
