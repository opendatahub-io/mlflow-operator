from types import SimpleNamespace

import pytest

from ..shared import TestContext
from . import validations as upgrade_validations
from .utils import build_upgrade_skill_markdown, compute_upgrade_skill_digest


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


_MCP_SERVER_CASE = {
    "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
    "version": "1.0.0",
    "description": "Static upgrade-test MCP server",
    "tags": {"upgrade-tag-key": "upgrade-tag-value"},
    "access_endpoint": {
        "url": "https://example.invalid/upgrade-mcp",
        "transport_type": "streamable-http",
    },
}


def test_validate_upgrade_mcp_servers_passes_when_state_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = SimpleNamespace(
        name=_MCP_SERVER_CASE["name"],
        description=_MCP_SERVER_CASE["description"],
        tags={"upgrade-tag-key": "upgrade-tag-value"},
    )
    endpoint = SimpleNamespace(
        url=_MCP_SERVER_CASE["access_endpoint"]["url"],
        server_version=_MCP_SERVER_CASE["version"],
        transport_type=_MCP_SERVER_CASE["access_endpoint"]["transport_type"],
    )

    monkeypatch.setattr(upgrade_validations.mlflow.genai, "get_mcp_server", lambda name: server)
    monkeypatch.setattr(
        upgrade_validations.mlflow.genai,
        "search_mcp_access_endpoints",
        lambda server_name: [endpoint],
    )

    test_context = TestContext(upgrade_state={"case": _MCP_SERVER_CASE})
    upgrade_validations.validate_upgrade_mcp_servers(test_context)


def test_validate_upgrade_mcp_servers_fails_on_tag_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = SimpleNamespace(
        name=_MCP_SERVER_CASE["name"],
        description=_MCP_SERVER_CASE["description"],
        tags={"upgrade-tag-key": "unexpected-value"},
    )

    monkeypatch.setattr(upgrade_validations.mlflow.genai, "get_mcp_server", lambda name: server)

    test_context = TestContext(upgrade_state={"case": _MCP_SERVER_CASE})
    with pytest.raises(AssertionError, match="tag 'upgrade-tag-key' mismatch"):
        upgrade_validations.validate_upgrade_mcp_servers(test_context)


def test_validate_upgrade_mcp_servers_fails_on_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = SimpleNamespace(
        name=_MCP_SERVER_CASE["name"],
        description=_MCP_SERVER_CASE["description"],
        tags={"upgrade-tag-key": "upgrade-tag-value"},
    )
    endpoint = SimpleNamespace(
        url=_MCP_SERVER_CASE["access_endpoint"]["url"],
        server_version="9.9.9",
        transport_type=_MCP_SERVER_CASE["access_endpoint"]["transport_type"],
    )

    monkeypatch.setattr(upgrade_validations.mlflow.genai, "get_mcp_server", lambda name: server)
    monkeypatch.setattr(
        upgrade_validations.mlflow.genai,
        "search_mcp_access_endpoints",
        lambda server_name: [endpoint],
    )

    test_context = TestContext(upgrade_state={"case": _MCP_SERVER_CASE})
    with pytest.raises(AssertionError, match="version mismatch"):
        upgrade_validations.validate_upgrade_mcp_servers(test_context)


def test_validate_upgrade_mcp_servers_fails_on_transport_type_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = SimpleNamespace(
        name=_MCP_SERVER_CASE["name"],
        description=_MCP_SERVER_CASE["description"],
        tags={"upgrade-tag-key": "upgrade-tag-value"},
    )
    endpoint = SimpleNamespace(
        url=_MCP_SERVER_CASE["access_endpoint"]["url"],
        server_version=_MCP_SERVER_CASE["version"],
        transport_type="unexpected-transport",
    )

    monkeypatch.setattr(upgrade_validations.mlflow.genai, "get_mcp_server", lambda name: server)
    monkeypatch.setattr(
        upgrade_validations.mlflow.genai,
        "search_mcp_access_endpoints",
        lambda server_name: [endpoint],
    )

    test_context = TestContext(upgrade_state={"case": _MCP_SERVER_CASE})
    with pytest.raises(AssertionError, match="transport_type mismatch"):
        upgrade_validations.validate_upgrade_mcp_servers(test_context)


def test_validate_pre_upgrade_version_allows_previous_version_post_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_validations, "get_requested_upgrade_phase", lambda: "post_upgrade")
    monkeypatch.setattr(upgrade_validations.Config, "UPGRADE_SUPPORTED_VERSION", "3.12.0")

    test_context = TestContext(upgrade_observed_state={"pre_upgrade_version": "3.10"})
    upgrade_validations.validate_pre_upgrade_version_configmap(test_context)


def test_validate_pre_upgrade_version_checks_supported_version_pre_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_validations, "get_requested_upgrade_phase", lambda: "pre_upgrade")
    monkeypatch.setattr(upgrade_validations.Config, "UPGRADE_SUPPORTED_VERSION", "v3.12.0")

    test_context = TestContext(upgrade_observed_state={"pre_upgrade_version": "3.10"})
    with pytest.raises(AssertionError, match=r"Expected ConfigMap version '3\.12'"):
        upgrade_validations.validate_pre_upgrade_version_configmap(test_context)


_SKILL_REGISTRY_CASE = {
    "name": "upgrade-test-skill",
    "description": "Static upgrade-test Skill",
    "skill_tags": {"upgrade-tag-key": "upgrade-tag-value"},
    "version_tags": {"upgrade-version-tag-key": "upgrade-version-tag-value"},
    "alias": "production",
    "versions": [{"body": "Version 1.\n"}, {"body": "Version 2.\n"}],
}
_SKILL_MARKDOWN = [
    build_upgrade_skill_markdown(_SKILL_REGISTRY_CASE, version)
    for version in _SKILL_REGISTRY_CASE["versions"]
]
_SKILL_DIGESTS = [compute_upgrade_skill_digest(markdown) for markdown in _SKILL_MARKDOWN]


def _skill_registry_observations() -> dict:
    return {
        "registered_skill_versions": [
            {"version": 1, "digest": _SKILL_DIGESTS[0]},
            {"version": 2, "digest": _SKILL_DIGESTS[1]},
        ],
        "skill_version_digests": {1: _SKILL_DIGESTS[0], 2: _SKILL_DIGESTS[1]},
        "skill": {
            "description": _SKILL_REGISTRY_CASE["description"],
            "tags": {"upgrade-tag-key": "upgrade-tag-value"},
            "aliases": {"production": 1},
            "latest_version": 2,
        },
        "skill_version_by_alias": {
            "version": 1,
            "digest": _SKILL_DIGESTS[0],
            "tags": {"upgrade-version-tag-key": "upgrade-version-tag-value"},
        },
        "latest_skill_version": 2,
        "skill_found_in_search": True,
        "pulled_skill_markdown": _SKILL_MARKDOWN[0],
        "pulled_skill_digest": _SKILL_DIGESTS[0],
    }


def _skill_registry_context(observed: dict) -> TestContext:
    return TestContext(
        upgrade_state={"skill_registry": _SKILL_REGISTRY_CASE},
        upgrade_observed_state=observed,
    )


def test_skill_digests_differ_between_versions() -> None:
    assert _SKILL_MARKDOWN[0].startswith("---\nname: upgrade-test-skill\n")
    assert _SKILL_DIGESTS[0] != _SKILL_DIGESTS[1]


def test_validate_registered_upgrade_skill_versions_passes_when_versions_match() -> None:
    upgrade_validations.validate_registered_upgrade_skill_versions(
        _skill_registry_context(_skill_registry_observations())
    )


@pytest.mark.parametrize(
    "registered",
    [
        [{"version": 1, "digest": _SKILL_DIGESTS[0]}, {"version": 1, "digest": _SKILL_DIGESTS[1]}],
        [{"version": 1, "digest": _SKILL_DIGESTS[0]}, {"version": 2, "digest": "sha256:other"}],
        [{"version": 1, "digest": _SKILL_DIGESTS[0]}],
        None,
    ],
    ids=["reused-version", "wrong-digest", "missing-version", "not-registered"],
)
def test_validate_registered_upgrade_skill_versions_fails_on_mismatch(registered) -> None:
    observed = _skill_registry_observations()
    observed["registered_skill_versions"] = registered

    with pytest.raises(AssertionError, match="Expected registered Skill versions"):
        upgrade_validations.validate_registered_upgrade_skill_versions(_skill_registry_context(observed))


def test_validate_upgrade_skill_registry_passes_when_state_matches() -> None:
    upgrade_validations.validate_upgrade_skill_registry(
        _skill_registry_context(_skill_registry_observations())
    )


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("skill", "description", "changed", "Skill description mismatch"),
        ("skill", "aliases", {"production": 2}, "Skill aliases mismatch"),
        ("skill", "latest_version", 1, "Skill latest_version mismatch"),
        ("skill", "tags", {}, "Skill tag 'upgrade-tag-key' mismatch"),
        ("skill_version_by_alias", "version", 2, "resolved to version 2"),
        ("skill_version_by_alias", "digest", "sha256:other", "digest mismatch"),
        ("skill_version_by_alias", "tags", {}, "version tag 'upgrade-version-tag-key' mismatch"),
    ],
)
def test_validate_upgrade_skill_registry_fails_on_read_mismatch(
    section: str, key: str, value: object, message: str
) -> None:
    observed = _skill_registry_observations()
    observed[section][key] = value

    with pytest.raises(AssertionError, match=message):
        upgrade_validations.validate_upgrade_skill_registry(_skill_registry_context(observed))


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("skill_version_digests", {1: _SKILL_DIGESTS[0]}, "Skill versions mismatch"),
        ("skill_version_digests", {1: _SKILL_DIGESTS[0], 2: "sha256:other"}, "Skill versions mismatch"),
        ("skill_found_in_search", False, "not returned by search_skills"),
        ("pulled_skill_markdown", _SKILL_MARKDOWN[1], "Pulled SKILL.md"),
        ("pulled_skill_digest", _SKILL_DIGESTS[1], "Pulled content digest mismatch"),
        ("latest_skill_version", 1, "Latest Skill version mismatch"),
    ],
)
def test_validate_upgrade_skill_registry_fails_on_version_search_or_pull_mismatch(
    key: str, value: object, message: str
) -> None:
    observed = _skill_registry_observations()
    observed[key] = value

    with pytest.raises(AssertionError, match=message):
        upgrade_validations.validate_upgrade_skill_registry(_skill_registry_context(observed))


_OCI_SKILL_CASE = {
    "name": "upgrade-test-oci-skill",
    "image": "registry.example.invalid/upgrade-tests/skill:v1",
    "subpath": "skills/upgrade",
    "digest": "a" * 64,
}


def _oci_skill_version(**overrides) -> dict:
    version = {
        "version": 1,
        "source_type": "oci",
        "image": _OCI_SKILL_CASE["image"],
        "subpath": _OCI_SKILL_CASE["subpath"],
        "digest": _OCI_SKILL_CASE["digest"],
    }
    version.update(overrides)
    return version


def _oci_skill_context(observed_versions) -> TestContext:
    return TestContext(
        upgrade_state={"skill_registry_oci": _OCI_SKILL_CASE},
        upgrade_observed_state={"oci_skill_versions": observed_versions},
    )


def test_validate_upgrade_oci_skill_version_passes_when_metadata_matches() -> None:
    upgrade_validations.validate_upgrade_oci_skill_version(_oci_skill_context([_oci_skill_version()]))


@pytest.mark.parametrize(
    "observed_versions",
    [
        [_oci_skill_version(source_type="git")],
        [_oci_skill_version(image="registry.example.invalid/upgrade-tests/skill:v2")],
        [_oci_skill_version(subpath=None)],
        [_oci_skill_version(digest="b" * 64)],
        [_oci_skill_version(), _oci_skill_version(version=2)],
        [],
        None,
    ],
    ids=["source-type", "image", "subpath", "digest", "extra-version", "no-versions", "not-collected"],
)
def test_validate_upgrade_oci_skill_version_fails_on_mismatch(observed_versions) -> None:
    with pytest.raises(AssertionError, match="OCI Skill versions mismatch"):
        upgrade_validations.validate_upgrade_oci_skill_version(_oci_skill_context(observed_versions))
