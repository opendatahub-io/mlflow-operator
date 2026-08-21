from types import SimpleNamespace

import pytest

from ..shared import TestContext
from . import validations as upgrade_validations


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


_MCP_SERVER_CASE = {
    "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
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
    endpoint = SimpleNamespace(url=_MCP_SERVER_CASE["access_endpoint"]["url"])

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
