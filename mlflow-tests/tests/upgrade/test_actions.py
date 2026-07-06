import pytest

from . import actions as upgrade_actions
from ..shared import TestContext


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_action_read_pre_upgrade_version_configmap_reads_shared_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_actions, "get_pre_upgrade_version_from_configmap", lambda: "3.10")

    test_context = TestContext()
    upgrade_actions.action_read_pre_upgrade_version_configmap(test_context)

    assert test_context.upgrade_observed_state["pre_upgrade_version"] == "3.10"


def test_action_start_upgrade_run_requires_active_experiment_id() -> None:
    test_context = TestContext(upgrade_state={"current_run": {"run_name": "upgrade-run"}})

    with pytest.raises(ValueError, match="active_experiment_id must be set"):
        upgrade_actions.action_start_upgrade_run(test_context)


def test_action_log_upgrade_text_artifact_rejects_path_traversal() -> None:
    test_context = TestContext(
        upgrade_state={
            "current_run": {
                "artifact_file": "../escape.txt",
                "artifact_content": "payload",
            }
        }
    )

    with pytest.raises(ValueError, match="Invalid artifact_file path"):
        upgrade_actions.action_log_upgrade_text_artifact(test_context)


def test_action_create_upgrade_model_version_requires_active_model_name() -> None:
    test_context = TestContext(
        current_run_id="run-123",
        upgrade_state={"current_model_version": {"description": "desc"}},
    )

    with pytest.raises(ValueError, match="active_model_name must be set"):
        upgrade_actions.action_create_upgrade_model_version(test_context)
