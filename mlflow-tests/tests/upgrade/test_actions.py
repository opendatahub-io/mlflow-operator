from pathlib import Path
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


_SKILL_REGISTRY_STATE = {
    "name": "upgrade-test-skill",
    "description": "Static upgrade-test Skill",
    "skill_tags": {"upgrade-tag-key": "upgrade-tag-value"},
    "version_tags": {"upgrade-version-tag-key": "upgrade-version-tag-value"},
    "alias": "production",
    "versions": [{"body": "Version 1.\n"}, {"body": "Version 2.\n"}],
}


class _FakeSkillClient:
    def __init__(self, get_skill_error: Exception | None = None) -> None:
        self.calls = []
        self.get_skill_error = get_skill_error

    def update_skill(self, **kwargs):
        self.calls.append(("update_skill", kwargs))

    def set_skill_tag(self, **kwargs):
        self.calls.append(("set_skill_tag", kwargs))

    def set_skill_version_tag(self, **kwargs):
        self.calls.append(("set_skill_version_tag", kwargs))

    def set_skill_alias(self, **kwargs):
        self.calls.append(("set_skill_alias", kwargs))

    def get_skill(self, **kwargs):
        self.calls.append(("get_skill", kwargs))
        if self.get_skill_error is not None:
            raise self.get_skill_error
        return SimpleNamespace(
            description=_SKILL_REGISTRY_STATE["description"],
            tags={"upgrade-tag-key": "upgrade-tag-value"},
            aliases={"production": 1},
            latest_version=2,
        )

    def search_skill_versions(self, **kwargs):
        self.calls.append(("search_skill_versions", kwargs))
        return [
            SimpleNamespace(version=1, digest="sha256:v1"),
            SimpleNamespace(version=2, digest="sha256:v2"),
        ]

    def get_skill_version_by_alias(self, **kwargs):
        return SimpleNamespace(version=1, digest="sha256:v1", tags={"k": "v"})

    def get_latest_skill_version(self, **kwargs):
        return SimpleNamespace(version=2)

    def search_skills(self, **kwargs):
        self.calls.append(("search_skills", kwargs))
        return [SimpleNamespace(name=kwargs["include_skill_identities"][0])]


def _missing_skill_error() -> MlflowException:
    return MlflowException(message="RESOURCE_DOES_NOT_EXIST: Skill does not exist")


def test_action_register_upgrade_skill_versions_registers_each_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = []

    def fake_register_skill(source, name):
        registered.append((name, (Path(source) / "SKILL.md").read_text(encoding="utf-8")))
        number = len(registered)
        return SimpleNamespace(name=name, organization="", version=number, digest=f"sha256:v{number}")

    monkeypatch.setattr(upgrade_actions.mlflow.genai, "register_skill", fake_register_skill)
    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        user_client=_FakeSkillClient(get_skill_error=_missing_skill_error()),
        upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE},
    )

    upgrade_actions.action_register_upgrade_skill_versions(test_context)

    assert registered == [
        ("upgrade-test-skill", "---\nname: upgrade-test-skill\ndescription: Static upgrade-test Skill\n---\nVersion 1.\n"),
        ("upgrade-test-skill", "---\nname: upgrade-test-skill\ndescription: Static upgrade-test Skill\n---\nVersion 2.\n"),
    ]
    assert test_context.upgrade_observed_state["registered_skill_versions"] == [
        {"version": 1, "digest": "sha256:v1"},
        {"version": 2, "digest": "sha256:v2"},
    ]
    # The seeded Skill must survive into the post-upgrade phase.
    assert test_context.skills_to_delete == {}


def test_action_register_upgrade_skill_versions_fails_if_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "register_skill",
        lambda **kwargs: pytest.fail("register_skill should not be called"),
    )
    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        user_client=_FakeSkillClient(),
        upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE},
    )

    with pytest.raises(AssertionError, match="already exists in workspace 'mlflow-upgrade-test-workspace'"):
        upgrade_actions.action_register_upgrade_skill_versions(test_context)


def test_action_register_upgrade_skill_versions_propagates_unexpected_lookup_errors() -> None:
    test_context = TestContext(
        user_client=_FakeSkillClient(get_skill_error=MlflowException(message="INTERNAL_ERROR: boom")),
        upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE},
    )

    with pytest.raises(MlflowException, match="boom"):
        upgrade_actions.action_register_upgrade_skill_versions(test_context)


def test_action_tag_and_alias_upgrade_skill_requires_registered_versions() -> None:
    test_context = TestContext(upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE})

    with pytest.raises(ValueError, match="must be registered before tagging"):
        upgrade_actions.action_tag_and_alias_upgrade_skill(test_context)


def test_action_tag_and_alias_upgrade_skill_targets_first_version() -> None:
    client = _FakeSkillClient()
    test_context = TestContext(
        user_client=client,
        upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE},
        upgrade_observed_state={"registered_skill_versions": [{"version": 1}, {"version": 2}]},
    )

    upgrade_actions.action_tag_and_alias_upgrade_skill(test_context)

    assert client.calls == [
        ("update_skill", {"name": "upgrade-test-skill", "description": _SKILL_REGISTRY_STATE["description"]}),
        ("set_skill_tag", {"name": "upgrade-test-skill", "key": "upgrade-tag-key", "value": "upgrade-tag-value"}),
        (
            "set_skill_version_tag",
            {
                "name": "upgrade-test-skill",
                "version": 1,
                "key": "upgrade-version-tag-key",
                "value": "upgrade-version-tag-value",
            },
        ),
        ("set_skill_alias", {"name": "upgrade-test-skill", "alias": "production", "version": 1}),
    ]


def test_action_collect_upgrade_skill_observations_records_reads_and_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pulled_uris = []

    def fake_pull(uri, destination):
        pulled_uris.append(uri)
        Path(destination).mkdir()
        (Path(destination) / "SKILL.md").write_text("pulled content", encoding="utf-8")
        return str(destination)

    monkeypatch.setattr(upgrade_actions.mlflow.genai, "pull", fake_pull)
    monkeypatch.setattr(upgrade_actions, "compute_tree_digest", lambda root: "sha256:pulled")
    client = _FakeSkillClient()
    test_context = TestContext(
        user_client=client,
        upgrade_state={"skill_registry": _SKILL_REGISTRY_STATE},
    )

    upgrade_actions.action_collect_upgrade_skill_observations(test_context)

    observed = test_context.upgrade_observed_state
    assert pulled_uris == ["skills:/upgrade-test-skill@production"]
    assert observed["skill"]["aliases"] == {"production": 1}
    assert observed["skill"]["latest_version"] == 2
    assert observed["skill_version_digests"] == {1: "sha256:v1", 2: "sha256:v2"}
    assert observed["skill_version_by_alias"] == {"version": 1, "digest": "sha256:v1", "tags": {"k": "v"}}
    assert observed["latest_skill_version"] == 2
    assert observed["skill_found_in_search"] is True
    assert ("search_skills", {"include_skill_identities": ["upgrade-test-skill"]}) in client.calls
    assert observed["pulled_skill_markdown"] == "pulled content"
    assert observed["pulled_skill_digest"] == "sha256:pulled"


_OCI_SKILL_STATE = {
    "name": "upgrade-test-oci-skill",
    "image": "registry.example.invalid/upgrade-tests/skill:v1",
    "subpath": "skills/upgrade",
    "digest": "a" * 64,
}


class _FakeOciSkillClient(_FakeSkillClient):
    def create_skill_version(self, **kwargs):
        self.calls.append(("create_skill_version", kwargs))
        return SimpleNamespace(name=kwargs["name"], version=1)

    def search_skill_versions(self, **kwargs):
        self.calls.append(("search_skill_versions", kwargs))
        return [
            SimpleNamespace(
                version=1,
                source_type=SimpleNamespace(value="oci"),
                source=SimpleNamespace(image=_OCI_SKILL_STATE["image"], subpath=_OCI_SKILL_STATE["subpath"]),
                digest=_OCI_SKILL_STATE["digest"],
            )
        ]


def test_action_register_upgrade_oci_skill_version_registers_metadata_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade_actions.mlflow.genai,
        "register_skill",
        lambda **kwargs: pytest.fail("register_skill fetches the source and must not be called"),
    )
    client = _FakeOciSkillClient(get_skill_error=_missing_skill_error())
    test_context = TestContext(user_client=client, upgrade_state={"skill_registry_oci": _OCI_SKILL_STATE})

    upgrade_actions.action_register_upgrade_oci_skill_version(test_context)

    create_calls = [kwargs for call, kwargs in client.calls if call == "create_skill_version"]
    assert create_calls == [
        {
            "name": "upgrade-test-oci-skill",
            "source": upgrade_actions.OCISource(
                image="registry.example.invalid/upgrade-tests/skill:v1", subpath="skills/upgrade"
            ),
            "digest": "a" * 64,
        }
    ]
    assert test_context.skills_to_delete == {}


def test_action_register_upgrade_oci_skill_version_fails_if_already_exists() -> None:
    client = _FakeOciSkillClient()
    test_context = TestContext(
        active_workspace="mlflow-upgrade-test-workspace",
        user_client=client,
        upgrade_state={"skill_registry_oci": _OCI_SKILL_STATE},
    )

    with pytest.raises(AssertionError, match="'upgrade-test-oci-skill' already exists"):
        upgrade_actions.action_register_upgrade_oci_skill_version(test_context)
    assert not any(call == "create_skill_version" for call, _ in client.calls)


def test_action_collect_upgrade_oci_skill_versions_records_source_metadata() -> None:
    client = _FakeOciSkillClient()
    test_context = TestContext(user_client=client, upgrade_state={"skill_registry_oci": _OCI_SKILL_STATE})

    upgrade_actions.action_collect_upgrade_oci_skill_versions(test_context)

    assert ("search_skill_versions", {"name": "upgrade-test-oci-skill"}) in client.calls
    assert test_context.upgrade_observed_state["oci_skill_versions"] == [
        {
            "version": 1,
            "source_type": "oci",
            "image": "registry.example.invalid/upgrade-tests/skill:v1",
            "subpath": "skills/upgrade",
            "digest": "a" * 64,
        }
    ]
