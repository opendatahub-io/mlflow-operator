from types import SimpleNamespace

import pytest
from mlflow.exceptions import MlflowException

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


def test_action_ensure_upgrade_experiment_retries_transient_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transient_error = MlflowException(
        message=(
            "Failed to query /api/3.0/mlflow/server-info: "
            'HTTPSConnectionPool(...): Read timed out. (read timeout=3)'
        ),
        error_code="INTERNAL_ERROR",
    )
    get_calls = []
    sleep_calls = []

    def fake_get_experiment_by_name(name: str):
        get_calls.append(name)
        if len(get_calls) == 1:
            raise transient_error
        return None

    monkeypatch.setattr(upgrade_actions.mlflow, "get_experiment_by_name", fake_get_experiment_by_name)
    monkeypatch.setattr(upgrade_actions.mlflow, "create_experiment", lambda name: "exp-123")
    monkeypatch.setattr(upgrade_actions.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        upgrade_state={"current_experiment": {"experiment_name": "upgrade-exp"}},
    )

    upgrade_actions.action_ensure_upgrade_experiment(test_context)

    assert get_calls == ["upgrade-exp", "upgrade-exp"]
    assert sleep_calls == [upgrade_actions._UPGRADE_EXPERIMENT_SETUP_RETRY_DELAY_SECONDS]
    assert test_context.active_experiment_id == "exp-123"
    assert test_context.upgrade_observed_state["experiment_id"] == "exp-123"


def test_action_ensure_upgrade_experiment_reuses_existing_after_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    already_exists_error = MlflowException(
        message="Experiment already exists",
        error_code="RESOURCE_ALREADY_EXISTS",
    )
    existing = SimpleNamespace(experiment_id="exp-456")
    get_calls = []

    def fake_get_experiment_by_name(name: str):
        get_calls.append(name)
        return None if len(get_calls) == 1 else existing

    monkeypatch.setattr(upgrade_actions.mlflow, "get_experiment_by_name", fake_get_experiment_by_name)
    monkeypatch.setattr(
        upgrade_actions.mlflow,
        "create_experiment",
        lambda name: (_ for _ in ()).throw(already_exists_error),
    )

    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        upgrade_state={"current_experiment": {"experiment_name": "upgrade-exp"}},
    )

    upgrade_actions.action_ensure_upgrade_experiment(test_context)

    assert get_calls == ["upgrade-exp", "upgrade-exp"]
    assert test_context.active_experiment_id == "exp-456"
    assert test_context.upgrade_observed_state["experiment_id"] == "exp-456"


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


def test_action_ensure_upgrade_mcp_server_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_found = MlflowException(
        message="RESOURCE_DOES_NOT_EXIST: MCP server does not exist"
    )
    register_calls = []

    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "get_mcp_server",
        lambda name: (_ for _ in ()).throw(not_found),
    )
    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "register_mcp_server",
        lambda **kwargs: register_calls.append(kwargs),
    )

    test_context = TestContext(
        upgrade_state={
            "current_mcp_server": {
                "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
                "version": "1.0.0",
                "description": "Static upgrade-test MCP server",
            }
        }
    )

    upgrade_actions.action_ensure_upgrade_mcp_server(test_context)

    assert register_calls == [
        {
            "server_json": {
                "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
                "version": "1.0.0",
                "description": "Static upgrade-test MCP server",
            },
            "tools": None,
        }
    ]
    assert test_context.active_mcp_server_name == "io.opendatahub.upgrade-tests/upgrade-mcp-server-1"


def test_action_ensure_upgrade_mcp_server_fails_if_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "get_mcp_server",
        lambda name: SimpleNamespace(name=name),
    )

    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        upgrade_state={
            "current_mcp_server": {
                "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
                "version": "1.0.0",
                "description": "Static upgrade-test MCP server",
            }
        },
    )

    with pytest.raises(AssertionError, match="already exists"):
        upgrade_actions.action_ensure_upgrade_mcp_server(test_context)


def test_action_tag_upgrade_mcp_server_requires_active_mcp_server_name() -> None:
    test_context = TestContext(
        upgrade_state={"current_mcp_server": {"tags": {"key": "value"}}},
    )

    with pytest.raises(ValueError, match="active_mcp_server_name must be set"):
        upgrade_actions.action_tag_upgrade_mcp_server(test_context)


def test_action_tag_upgrade_mcp_server_sets_each_configured_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_tag_calls = []
    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "set_mcp_server_tag",
        lambda name, key, value: set_tag_calls.append((name, key, value)),
    )

    test_context = TestContext(
        active_mcp_server_name="io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
        upgrade_state={
            "current_mcp_server": {"tags": {"upgrade-tag-key": "upgrade-tag-value"}}
        },
    )

    upgrade_actions.action_tag_upgrade_mcp_server(test_context)

    assert set_tag_calls == [
        (
            "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
            "upgrade-tag-key",
            "upgrade-tag-value",
        )
    ]


def test_action_create_upgrade_mcp_access_endpoint_requires_active_mcp_server_name() -> None:
    test_context = TestContext(
        upgrade_state={
            "current_mcp_server": {
                "access_endpoint": {"url": "https://example.invalid/upgrade-mcp", "transport_type": "streamable-http"},
                "version": "1.0.0",
            }
        }
    )

    with pytest.raises(ValueError, match="active_mcp_server_name must be set"):
        upgrade_actions.action_create_upgrade_mcp_access_endpoint(test_context)


def test_action_create_upgrade_mcp_access_endpoint_records_endpoint_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_endpoint = SimpleNamespace(id="endpoint-123")
    create_calls = []

    def fake_create_mcp_access_endpoint(**kwargs):
        create_calls.append(kwargs)
        return created_endpoint

    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "create_mcp_access_endpoint",
        fake_create_mcp_access_endpoint,
    )

    test_context = TestContext(
        active_mcp_server_name="io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
        upgrade_state={
            "current_mcp_server": {
                "version": "1.0.0",
                "access_endpoint": {
                    "url": "https://example.invalid/upgrade-mcp",
                    "transport_type": "streamable-http",
                },
            }
        },
    )

    upgrade_actions.action_create_upgrade_mcp_access_endpoint(test_context)

    assert create_calls == [
        {
            "server_name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
            "url": "https://example.invalid/upgrade-mcp",
            "transport_type": "streamable-http",
            "server_version": "1.0.0",
        }
    ]
    assert test_context.upgrade_observed_state["mcp_access_endpoint_id"] == "endpoint-123"
