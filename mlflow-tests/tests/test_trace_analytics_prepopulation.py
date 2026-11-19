"""Live predecessor-schema backfill and rerun coverage.

Requires explicit ``trace_analytics_prepopulation`` marker selection and an
eligible PostgreSQL harness run; plain pytest deselects this suite.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import ClassVar

import mlflow
import pytest

from .actions import (
    action_create_experiment,
    action_prepare_trace_prepopulation,
    action_run_first_prepopulation_job,
    action_run_second_prepopulation_job,
    action_seed_additional_prepopulation_traces,
    action_seed_initial_prepopulation_traces,
)
from .base import TestBase
from .actions.trace_prepopulation_actions import (
    TRACE_MODEL_NAME,
    TRACE_MODEL_PROVIDER,
    _database_query,
    _seed_traces,
)
from kubernetes import client
from .constants.config import Config
from .shared import TestData, TestStep
from .validations import (
    validate_additional_prepopulation_traces_seeded,
    validate_prepopulation_experiment_created,
    validate_first_prepopulation_job,
    validate_initial_prepopulation_traces_seeded,
    validate_second_prepopulation_job,
    validate_trace_prepopulation_prepared,
)

logger = logging.getLogger(__name__)


@pytest.mark.trace_analytics_prepopulation
@pytest.mark.skipif(
    not Config.TRACE_ANALYTICS_PREPOPULATION_ENABLED,
    reason=(
        "trace analytics prepopulation requires an eligible harness run; set "
        "trace_analytics_prepopulation_enabled=true and trace_prepopulate_image"
    ),
)
class TestTraceAnalyticsPrepopulation(TestBase):
    """Exercise the prepopulation Job workflow using shared actions/validations."""

    test_scenarios: ClassVar[list[TestData]] = [
        TestData(
            test_name=(
                "Prepopulation Job backfills trace analytics on a live deployment "
                "and a rerun skips already-correct rows"
            ),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps=[
                TestStep(
                    action_func=action_prepare_trace_prepopulation,
                    validate_func=validate_trace_prepopulation_prepared,
                ),
                TestStep(
                    action_func=action_create_experiment,
                    validate_func=validate_prepopulation_experiment_created,
                ),
                TestStep(
                    action_func=action_seed_initial_prepopulation_traces,
                    validate_func=validate_initial_prepopulation_traces_seeded,
                ),
                TestStep(
                    action_func=action_run_first_prepopulation_job,
                    validate_func=validate_first_prepopulation_job,
                ),
                TestStep(
                    action_func=action_seed_additional_prepopulation_traces,
                    validate_func=validate_additional_prepopulation_traces_seeded,
                ),
                TestStep(
                    action_func=action_run_second_prepopulation_job,
                    validate_func=validate_second_prepopulation_job,
                ),
            ],
        ),
    ]

    @pytest.mark.parametrize(
        "test_data", test_scenarios, ids=lambda data: data.test_name
    )
    def test_trace_analytics_prepopulation_job(self, test_data: TestData) -> None:
        logger.info("=" * 80)
        logger.info("Starting test: %s", test_data.test_name)
        logger.info("Workspace: %s", test_data.workspace_to_use)
        logger.info("=" * 80)

        self.test_context.last_error = None
        self.test_context.user_client = self.admin_client
        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)

        self._execute_test_steps(test_data)

        results_dir = os.environ.get("TEST_RESULTS_DIR")
        if results_dir:
            state = self.test_context.prepopulation_state
            late = _seed_traces(self.test_context, state, 1, "migration-only")
            if _database_query(
                client.CoreV1Api(),
                state,
                "SELECT count(*) FROM trace_info t JOIN spans s "
                "ON s.trace_id = t.request_id WHERE t.request_id = :'trace_id' "
                "AND s.span_id IN (:'root', :'child') AND t.trace_name IS NULL "
                "AND s.model_name IS NULL AND s.model_provider IS NULL",
                params={
                    "trace_id": late[0]["trace_id"],
                    "root": late[0]["root_span_id"],
                    "child": late[0]["child_span_id"],
                },
            ) != "2":
                pytest.fail("Late trace and spans must still need backfill before migration")
            expected = [
                {
                    "trace_id": payload["trace_id"],
                    "trace_name": payload["trace_name"],
                    "span_id": payload[key],
                    "model_name": TRACE_MODEL_NAME,
                    "model_provider": TRACE_MODEL_PROVIDER,
                }
                for payload in state["payloads"]["initial"] + state["payloads"]["additional"] + late
                for key in ("root_span_id", "child_span_id")
            ]
            (Path(results_dir) / "trace-migration-expectations.json").write_text(
                json.dumps(expected), encoding="utf-8"
            )
