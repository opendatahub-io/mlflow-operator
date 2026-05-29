import pytest

from .shared import TestContext
from .validations import upgrade_validations


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


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
