"""Atomic action helpers for MLflow upgrade pre/post validation."""

from __future__ import annotations

import logging
import os
import tempfile
import time

import mlflow
from mlflow.entities.span import NO_OP_SPAN_TRACE_ID
from mlflow.exceptions import MlflowException
from mlflow.tracing.attachments import Attachment
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME
import requests

from ..constants.config import Config
from ..http_utils import get_mlflow_base_uri, get_requests_verify_value
from ..shared import TestContext
from .utils import (
    get_pre_upgrade_version_from_configmap,
    get_supported_upgrade_version_from_env,
    upsert_pre_upgrade_version_configmap,
)

logger = logging.getLogger(__name__)


def make_upgrade_state_action(action_name: str, **state_updates):
    """Return an action that injects reusable upgrade state into the test context."""

    def action(test_context: TestContext) -> None:
        test_context.upgrade_state.update(state_updates)

    action.__name__ = action_name
    return action


def _require_upgrade_payload(test_context: TestContext, key: str) -> dict:
    payload = test_context.upgrade_state.get(key)
    if payload is None:
        raise ValueError(f"test_context.upgrade_state['{key}'] must be set before this action")
    return payload


def _require_upgrade_value(test_context: TestContext, key: str):
    if key not in test_context.upgrade_state:
        raise ValueError(f"test_context.upgrade_state['{key}'] must be set before this action")
    return test_context.upgrade_state[key]


def _is_missing_resource_error(exc: MlflowException) -> bool:
    error_code = getattr(exc, "error_code", "")
    message = str(exc)
    return error_code == "RESOURCE_DOES_NOT_EXIST" or "RESOURCE_DOES_NOT_EXIST" in message


def action_write_pre_upgrade_version_configmap(test_context: TestContext) -> None:
    """Write the normalized supported MLflow version into the shared ConfigMap."""
    version = get_supported_upgrade_version_from_env()
    upsert_pre_upgrade_version_configmap(version)
    test_context.upgrade_observed_state["pre_upgrade_version"] = version


def action_read_pre_upgrade_version_configmap(test_context: TestContext) -> None:
    """Read the normalized pre-upgrade MLflow version from the shared ConfigMap."""
    test_context.upgrade_observed_state["pre_upgrade_version"] = (
        get_pre_upgrade_version_from_configmap()
    )


def action_ensure_upgrade_experiment(test_context: TestContext) -> None:
    """Create the current upgrade experiment if needed and store its identifier."""
    experiment = _require_upgrade_payload(test_context, "current_experiment")
    experiment_name = experiment["experiment_name"]
    existing = mlflow.get_experiment_by_name(experiment_name)
    if existing is not None:
        raise AssertionError(
            f"Upgrade experiment '{experiment_name}' already exists in workspace "
            f"'{test_context.active_workspace}'. Clean the static upgrade workspace before reseeding."
        )
    experiment_id = mlflow.create_experiment(experiment_name)
    logger.info("Created upgrade experiment '%s' with id %s", experiment_name, experiment_id)

    test_context.active_experiment_id = experiment_id
    test_context.upgrade_state["active_experiment_name"] = experiment_name
    test_context.upgrade_observed_state["experiment_id"] = experiment_id


def action_start_upgrade_run(test_context: TestContext) -> None:
    """Start the current upgrade run inside the active experiment."""
    run_payload = _require_upgrade_payload(test_context, "current_run")
    if not test_context.active_experiment_id:
        raise ValueError("active_experiment_id must be set before starting an upgrade run")
    run = mlflow.start_run(
        experiment_id=test_context.active_experiment_id,
        run_name=run_payload["run_name"],
    )
    test_context.current_run_id = run.info.run_id
    test_context.upgrade_observed_state.setdefault("run_ids", {})[run_payload["run_name"]] = (
        run.info.run_id
    )


def action_log_upgrade_run_params(test_context: TestContext) -> None:
    """Log the configured parameter set for the active upgrade run."""
    run_payload = _require_upgrade_payload(test_context, "current_run")
    for key, value in run_payload["params"].items():
        mlflow.log_param(key, value)


def action_log_upgrade_run_metrics(test_context: TestContext) -> None:
    """Log the configured metric set for the active upgrade run."""
    run_payload = _require_upgrade_payload(test_context, "current_run")
    for key, value in run_payload["metrics"].items():
        mlflow.log_metric(key, value)


def action_log_upgrade_text_artifact(test_context: TestContext) -> None:
    """Create and log the configured upgrade artifact under its stable filename."""
    run_payload = _require_upgrade_payload(test_context, "current_run")

    target_name = run_payload["artifact_file"]
    normalized_name = os.path.normpath(target_name)
    if (
        os.path.isabs(normalized_name)
        or normalized_name.startswith("..")
        or "/" in target_name
        or "\\" in target_name
    ):
        raise ValueError(f"Invalid artifact_file path: {target_name!r}")
    with tempfile.TemporaryDirectory(prefix="mlflow-upgrade-artifact-") as artifact_dir:
        target_path = os.path.join(artifact_dir, normalized_name)
        artifact_dir_realpath = os.path.realpath(artifact_dir)
        target_path_realpath = os.path.realpath(target_path)
        if os.path.commonpath([artifact_dir_realpath, target_path_realpath]) != artifact_dir_realpath:
            raise ValueError(
                f"artifact_file must stay within the temporary artifact directory: {target_name!r}"
            )
        with open(target_path, "w", encoding="utf-8") as target:
            target.write(run_payload["artifact_content"])
        mlflow.log_artifact(target_path)


def action_create_upgrade_trace(test_context: TestContext) -> None:
    """Create the current upgrade trace with session metadata."""
    session_payload = _require_upgrade_payload(test_context, "current_trace_session")
    trace_payload = _require_upgrade_payload(test_context, "current_trace")
    if not test_context.active_experiment_id:
        raise ValueError("active_experiment_id must be set before creating an upgrade trace")

    inputs = dict(trace_payload["inputs"])
    if "attachment_content" in trace_payload:
        attachment_name = trace_payload["inputs"]["attachment_name"]
        inputs["attachment"] = Attachment(
            content_type="text/plain",
            content_bytes=trace_payload["attachment_content"].encode("utf-8"),
        )
        test_context.upgrade_observed_state.setdefault("trace_attachment_names", {})[
            trace_payload["trace_name"]
        ] = attachment_name
    span = mlflow.start_span_no_context(
        name=trace_payload["trace_name"],
        inputs=inputs,
        metadata={
            "mlflow.trace.session": session_payload["session_id"],
            "mlflow.trace.user": session_payload["user"],
        },
        experiment_id=test_context.active_experiment_id,
    )
    span.set_outputs(trace_payload["outputs"])
    trace_id = getattr(span, "request_id", None) or getattr(span, "trace_id", None)
    if trace_id is None:
        raise AssertionError(
            f"Trace '{trace_payload['trace_name']}' did not return a trace/request id"
        )
    if trace_id == NO_OP_SPAN_TRACE_ID:
        raise AssertionError(
            f"Trace '{trace_payload['trace_name']}' returned a no-op span trace id"
        )
    span.end()
    mlflow.flush_trace_async_logging()
    test_context.current_trace_id = trace_id
    test_context.current_trace_name = trace_payload["trace_name"]
    test_context.upgrade_observed_state.setdefault("trace_ids", {})[trace_payload["trace_name"]] = (
        trace_id
    )


def action_collect_upgrade_trace_observations(test_context: TestContext) -> None:
    """Collect persisted trace data for the active upgrade case."""
    case = _require_upgrade_payload(test_context, "case")
    experiment = mlflow.get_experiment_by_name(case["experiment_name"])
    if experiment is None:
        raise ValueError(f"Experiment '{case['experiment_name']}' not found")

    expected_traces_by_session = {
        session_payload["session_id"]: {
            trace_payload["trace_name"] for trace_payload in session_payload["traces"]
        }
        for session_payload in case["sessions"]
    }
    expected_attachment_traces = {
        trace_payload["trace_name"]
        for session_payload in case["sessions"]
        for trace_payload in session_payload["traces"]
        if "attachment_content" in trace_payload
    }

    observed_by_session = {}
    attachment_contents = {}
    for attempt in range(5):
        traces = test_context.user_client.search_traces(
            experiment_ids=[experiment.experiment_id],
            max_results=100,
        )

        observed_by_session = {}
        attachment_contents = {}
        for trace in traces:
            metadata = trace.info.trace_metadata or {}
            session_id = metadata.get("mlflow.trace.session")
            if not session_id or not trace.data.spans:
                continue
            root_span = trace.data.spans[0]
            observed_by_session.setdefault(session_id, {})[root_span.name] = trace

            attachment_ref = root_span.inputs.get("attachment")
            parsed_ref = Attachment.parse_ref(attachment_ref) if attachment_ref else None
            if parsed_ref is None:
                continue

            response = requests.get(
                f"{get_mlflow_base_uri()}/ajax-api/3.0/mlflow/get-trace-artifact",
                params={
                    "request_id": trace.info.trace_id,
                    "path": parsed_ref["attachment_id"],
                },
                headers={
                    "Authorization": f"Bearer {Config.K8_API_TOKEN}",
                    WORKSPACE_HEADER_NAME: test_context.active_workspace,
                },
                verify=get_requests_verify_value(),
                timeout=Config.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            attachment_contents[root_span.name] = response.content.decode("utf-8")

        if all(
            expected_trace_names.issubset(set(observed_by_session.get(session_id, {})))
            for session_id, expected_trace_names in expected_traces_by_session.items()
        ) and expected_attachment_traces.issubset(set(attachment_contents)):
            break
        if attempt < 4:
            time.sleep(1)

    test_context.upgrade_observed_state["traces_by_session"] = observed_by_session
    test_context.upgrade_observed_state["trace_attachment_contents"] = attachment_contents


def action_ensure_upgrade_registered_model(test_context: TestContext) -> None:
    """Create the current upgrade registered model if needed."""
    model_payload = _require_upgrade_payload(test_context, "current_registered_model")
    model_name = model_payload["name"]
    try:
        test_context.user_client.get_registered_model(model_name)
        raise AssertionError(
            f"Upgrade registered model '{model_name}' already exists in workspace "
            f"'{test_context.active_workspace}'. Clean the static upgrade workspace before reseeding."
        )
    except MlflowException as exc:
        if not _is_missing_resource_error(exc):
            raise
        test_context.user_client.create_registered_model(model_name)
        logger.info("Created upgrade registered model '%s'", model_name)
    test_context.active_model_name = model_name


def action_create_upgrade_model_version(test_context: TestContext) -> None:
    """Create a model version for the current registered model from the active run."""
    version_payload = _require_upgrade_payload(test_context, "current_model_version")
    if not test_context.current_run_id:
        raise ValueError("current_run_id must be set before creating a model version")
    if not test_context.active_model_name:
        raise ValueError("active_model_name must be set before creating a model version")

    source = f"runs:/{test_context.current_run_id}/model"
    model_version = test_context.user_client.create_model_version(
        name=test_context.active_model_name,
        source=source,
        run_id=test_context.current_run_id,
        description=version_payload["description"],
    )
    test_context.upgrade_observed_state.setdefault("model_versions", {}).setdefault(
        test_context.active_model_name,
        [],
    ).append(model_version.version)


def action_ensure_upgrade_prompt(test_context: TestContext) -> None:
    """Create the current upgrade prompt if needed."""
    prompt_payload = _require_upgrade_payload(test_context, "current_prompt")
    prompt_name = prompt_payload["name"]
    try:
        test_context.user_client.create_prompt(
            name=prompt_name,
            description=prompt_payload["description"],
        )
        logger.info("Created upgrade prompt '%s'", prompt_name)
    except MlflowException as exc:
        error_code = getattr(exc, "error_code", "")
        if error_code == "RESOURCE_ALREADY_EXISTS" or "RESOURCE_ALREADY_EXISTS" in str(exc):
            raise AssertionError(
                f"Upgrade prompt '{prompt_name}' already exists in workspace "
                f"'{test_context.active_workspace}'. Clean the static upgrade workspace before reseeding."
            ) from exc
        raise
    test_context.upgrade_state["active_prompt_name"] = prompt_name


def action_create_upgrade_prompt_version(test_context: TestContext) -> None:
    """Create the current upgrade prompt version under the active prompt."""
    prompt_version = _require_upgrade_payload(test_context, "current_prompt_version")
    prompt_name = _require_upgrade_value(test_context, "active_prompt_name")
    created = test_context.user_client.create_prompt_version(
        name=prompt_name,
        template=prompt_version["template"],
        description=prompt_version["description"],
    )
    test_context.upgrade_observed_state.setdefault("prompt_versions", {}).setdefault(
        prompt_name,
        [],
    ).append(created.version)
