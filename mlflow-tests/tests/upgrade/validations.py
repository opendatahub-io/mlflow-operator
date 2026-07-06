"""Validation helpers for MLflow upgrade pre/post scenarios."""

from __future__ import annotations

import logging

import mlflow
import pytest

from ..constants.config import Config
from ..shared import TestContext
from .utils import get_requested_upgrade_phase, normalize_mlflow_version

logger = logging.getLogger(__name__)


def _require_case_payload(test_context: TestContext, key: str) -> dict:
    payload = test_context.upgrade_state.get(key)
    if payload is None:
        raise AssertionError(f"Upgrade payload '{key}' is not set on the test context")
    return payload

def validate_pre_upgrade_version_configmap(test_context: TestContext) -> None:
    """Validate that the observed pre-upgrade version is normalized and correct."""
    observed = test_context.upgrade_observed_state.get("pre_upgrade_version")
    assert observed, "Expected a normalized pre-upgrade version for upgrade validation"
    normalized_observed = normalize_mlflow_version(observed)
    assert observed == normalized_observed, (
        f"Expected pre-upgrade version '{observed}' to already be normalized to 'x.y'"
    )
    if get_requested_upgrade_phase() == "pre_upgrade":
        expected = normalize_mlflow_version(Config.UPGRADE_SUPPORTED_VERSION)
        assert observed == expected, f"Expected ConfigMap version '{expected}', got '{observed}'"


def validate_upgrade_experiment_runs(test_context: TestContext) -> None:
    """Validate the upgrade experiment/runs/params/metrics/artifacts scenario."""
    case = _require_case_payload(test_context, "case")
    experiment = mlflow.get_experiment_by_name(case["experiment_name"])
    assert experiment is not None, f"Experiment '{case['experiment_name']}' not found"

    runs = mlflow.search_runs([experiment.experiment_id], output_format="list")
    runs_by_name = {
        run.data.tags.get("mlflow.runName"): run
        for run in runs
        if run.data.tags.get("mlflow.runName")
    }

    for run_payload in case["runs"]:
        run = runs_by_name.get(run_payload["run_name"])
        assert run is not None, f"Run '{run_payload['run_name']}' not found"
        for key, value in run_payload["params"].items():
            assert run.data.params.get(key) == value, (
                f"Run '{run_payload['run_name']}' param '{key}' mismatch: "
                f"expected '{value}', got '{run.data.params.get(key)}'"
            )
        for key, value in run_payload["metrics"].items():
            assert run.data.metrics.get(key) == pytest.approx(value), (
                f"Run '{run_payload['run_name']}' metric '{key}' mismatch: "
                f"expected '{value}', got '{run.data.metrics.get(key)}'"
            )
        downloaded = mlflow.artifacts.download_artifacts(
            run_id=run.info.run_id,
            artifact_path=run_payload["artifact_file"],
        )
        with open(downloaded, "r", encoding="utf-8") as handle:
            actual = handle.read()
        assert actual == run_payload["artifact_content"], (
            f"Artifact '{run_payload['artifact_file']}' content mismatch: "
            f"expected '{run_payload['artifact_content']}', got '{actual}'"
        )


def validate_upgrade_trace_sessions(test_context: TestContext) -> None:
    """Validate the upgrade trace/session scenario."""
    case = _require_case_payload(test_context, "case")
    traces_by_session = test_context.upgrade_observed_state.get("traces_by_session", {})
    attachment_contents = test_context.upgrade_observed_state.get("trace_attachment_contents", {})

    for session_payload in case["sessions"]:
        session_traces = traces_by_session.get(session_payload["session_id"], {})
        assert len(session_traces) >= len(session_payload["traces"]), (
            f"Expected at least {len(session_payload['traces'])} traces for session "
            f"'{session_payload['session_id']}', found {len(session_traces)}"
        )
        for trace_payload in session_payload["traces"]:
            trace = session_traces.get(trace_payload["trace_name"])
            assert trace is not None, (
                f"Trace '{trace_payload['trace_name']}' missing from session "
                f"'{session_payload['session_id']}'"
            )
            metadata = trace.info.trace_metadata or {}
            assert metadata.get("mlflow.trace.session") == session_payload["session_id"]
            assert metadata.get("mlflow.trace.user") == session_payload["user"]

            root_span = trace.data.spans[0]
            assert root_span.inputs.get("message") == trace_payload["inputs"]["message"]
            assert root_span.outputs.get("result") == trace_payload["outputs"]["result"]

            if "attachment_content" not in trace_payload:
                continue

            actual_content = attachment_contents.get(trace_payload["trace_name"])
            assert actual_content == trace_payload["attachment_content"], (
                f"Attachment content mismatch for trace '{trace_payload['trace_name']}': "
                f"expected '{trace_payload['attachment_content']}', got '{actual_content}'"
            )


def validate_upgrade_registered_models(test_context: TestContext) -> None:
    """Validate the upgrade registered-model/model-version scenario."""
    case = _require_case_payload(test_context, "case")
    experiment = mlflow.get_experiment_by_name(case["experiment_name"])
    assert experiment is not None, f"Experiment '{case['experiment_name']}' not found"

    runs = mlflow.search_runs([experiment.experiment_id], output_format="list")
    runs_by_name = {
        run.data.tags.get("mlflow.runName"): run
        for run in runs
        if run.data.tags.get("mlflow.runName")
    }

    for model_payload in case["models"]:
        registered_model = test_context.user_client.get_registered_model(model_payload["name"])
        assert registered_model is not None, f"Registered model '{model_payload['name']}' not found"
        for index, version_payload in enumerate(model_payload["versions"], start=1):
            version = test_context.user_client.get_model_version(model_payload["name"], str(index))
            assert str(version.version) == str(index)
            seeded_run = runs_by_name.get(version_payload["run_name"])
            assert seeded_run is not None, (
                f"Seed run '{version_payload['run_name']}' not found for model "
                f"'{model_payload['name']}'"
            )
            assert version.description == version_payload["description"], (
                f"Registered model '{model_payload['name']}' version '{index}' description mismatch: "
                f"expected '{version_payload['description']}', got '{version.description}'"
            )
            assert version.run_id == seeded_run.info.run_id, (
                f"Registered model '{model_payload['name']}' version '{index}' run_id mismatch: "
                f"expected '{seeded_run.info.run_id}', got '{version.run_id}'"
            )
            assert seeded_run.info.run_id in version.source, (
                f"Registered model '{model_payload['name']}' version '{index}' source "
                f"does not reference the seeded run '{seeded_run.info.run_id}': {version.source}"
            )


def validate_upgrade_prompts(test_context: TestContext) -> None:
    """Validate the upgrade prompt/prompt-version scenario."""
    case = _require_case_payload(test_context, "case")
    for prompt_payload in case["prompts"]:
        prompt = test_context.user_client.get_prompt(prompt_payload["name"])
        assert prompt is not None, f"Prompt '{prompt_payload['name']}' not found"
        assert prompt.description == prompt_payload["description"], (
            f"Prompt '{prompt_payload['name']}' description mismatch: "
            f"expected '{prompt_payload['description']}', got '{prompt.description}'"
        )
        for index, version_payload in enumerate(prompt_payload["versions"], start=1):
            version = test_context.user_client.get_prompt_version(prompt_payload["name"], str(index))
            assert str(version.version) == str(index)
            assert version.description == version_payload["description"], (
                f"Prompt '{prompt_payload['name']}' version '{index}' description mismatch: "
                f"expected '{version_payload['description']}', got '{version.description}'"
            )
            assert version.template == version_payload["template"], (
                f"Prompt '{prompt_payload['name']}' version '{index}' template mismatch"
            )
