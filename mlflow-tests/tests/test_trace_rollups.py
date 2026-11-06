"""Live SQL trace-rollup smoke coverage against the harness PostgreSQL backend."""

from __future__ import annotations

import logging
from typing import ClassVar

import mlflow
import pytest

from .actions import (
    action_cleanup_trace_rollups,
    action_create_experiment,
    action_prepare_trace_rollups,
    action_query_trace_rollup_metrics,
    action_read_trace_rollup_rows,
    action_read_trace_rollup_sources,
    action_run_incremental_trace_rollup_job,
    action_run_trace_rollup_job,
    action_seed_additional_trace_rollup_traces,
    action_seed_trace_rollup_traces,
)
from .base import TestBase
from .constants.config import Config
from .shared import TestData, TestStep
from .validations import (
    validate_additional_trace_rollup_traces_seeded,
    validate_trace_rollup_api_metrics,
    validate_trace_rollup_experiment_created,
    validate_trace_rollup_job_completed,
    validate_trace_rollup_incremental_job_completed,
    validate_trace_rollup_rows,
    validate_trace_rollup_traces_readable,
    validate_trace_rollup_traces_seeded,
    validate_trace_rollups_ready,
)

logger = logging.getLogger(__name__)


@pytest.mark.smoke
@pytest.mark.skipif(
    not Config.TRACE_ROLLUPS_ENABLED,
    reason="requires the harness PostgreSQL backend and operator rollup CronJob",
)
class TestTraceRollups(TestBase):
    """Exercise the SQL rollup CronJob using shared actions and validations."""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name=(
                "SQL trace rollups include traces added after the first CronJob run"
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_prepare_trace_rollups,
                    validate_func=validate_trace_rollups_ready,
                ),
                TestStep(
                    action_func=action_create_experiment,
                    validate_func=validate_trace_rollup_experiment_created,
                ),
                TestStep(
                    action_func=action_seed_trace_rollup_traces,
                    validate_func=validate_trace_rollup_traces_seeded,
                ),
                TestStep(
                    action_func=action_run_trace_rollup_job,
                    validate_func=validate_trace_rollup_job_completed,
                ),
                TestStep(
                    action_func=action_read_trace_rollup_rows,
                    validate_func=validate_trace_rollup_rows,
                ),
                TestStep(
                    action_func=action_query_trace_rollup_metrics,
                    validate_func=validate_trace_rollup_api_metrics,
                ),
                TestStep(
                    action_func=action_read_trace_rollup_sources,
                    validate_func=validate_trace_rollup_traces_readable,
                ),
                TestStep(
                    action_func=action_seed_additional_trace_rollup_traces,
                    validate_func=validate_additional_trace_rollup_traces_seeded,
                ),
                TestStep(
                    action_func=action_run_incremental_trace_rollup_job,
                    validate_func=validate_trace_rollup_incremental_job_completed,
                ),
                TestStep(
                    action_func=action_read_trace_rollup_rows,
                    validate_func=validate_trace_rollup_rows,
                ),
                TestStep(
                    action_func=action_query_trace_rollup_metrics,
                    validate_func=validate_trace_rollup_api_metrics,
                ),
                TestStep(
                    action_func=action_read_trace_rollup_sources,
                    validate_func=validate_trace_rollup_traces_readable,
                ),
            ],
        ),
    ]

    @pytest.mark.parametrize("test_data", test_scenarios, ids=lambda data: data.test_name)
    def test_trace_rollup_cronjob(self, test_data: TestData) -> None:
        logger.info("=" * 80)
        logger.info("Starting test: %s", test_data.test_name)
        logger.info("Workspace: %s", test_data.workspace_to_use)
        logger.info("=" * 80)

        self.test_context.last_error = None
        self.test_context.user_client = self.admin_client
        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)

        test_failed = False
        try:
            self._execute_test_steps(test_data)
        except BaseException:
            test_failed = True
            raise
        finally:
            try:
                action_cleanup_trace_rollups(self.test_context)
            except Exception:
                if not test_failed:
                    raise
                logger.exception("Trace-rollup smoke cleanup failed after a test failure")
