import pytest

from . import utils as upgrade_utils


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_normalize_mlflow_version_strips_prefix_and_suffix() -> None:
    assert upgrade_utils.normalize_mlflow_version("v3.12.0+rhaiv.3") == "3.12"
    assert upgrade_utils.normalize_mlflow_version("3.10.1") == "3.10"


def test_parse_minimum_version_from_path_extracts_major_minor() -> None:
    assert (
        upgrade_utils.parse_minimum_version_from_path("tests/upgrade/pre_upgrade/test_3_10.py")
        == (3, 10)
    )
    assert upgrade_utils.parse_minimum_version_from_path("tests/upgrade/pre_upgrade/test_upgrade.py") is None
    assert upgrade_utils.parse_minimum_version_from_path("tests/upgrade/pre_upgrade/test_3_x.py") is None


def test_should_run_versioned_test_allows_non_versioned_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_utils, "get_effective_pre_upgrade_version", lambda phase=None: "3.12")
    assert upgrade_utils.should_run_versioned_test("tests/test_experiments.py", "pre_upgrade") is True


def test_should_run_versioned_test_respects_minimum_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_utils, "get_effective_pre_upgrade_version", lambda phase=None: "3.12")
    assert upgrade_utils.should_run_versioned_test("tests/upgrade/pre_upgrade/test_3_10.py", "pre_upgrade") is True

    monkeypatch.setattr(upgrade_utils, "get_effective_pre_upgrade_version", lambda phase=None: "3.9")
    assert upgrade_utils.should_run_versioned_test("tests/upgrade/pre_upgrade/test_3_10.py", "pre_upgrade") is False


def test_get_effective_pre_upgrade_version_uses_configmap_for_post_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade_utils,
        "get_pre_upgrade_version_from_configmap",
        lambda: "3.10",
    )

    assert upgrade_utils.get_effective_pre_upgrade_version("post_upgrade") == "3.10"


def test_get_effective_pre_upgrade_version_returns_none_when_configmap_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing() -> str:
        raise upgrade_utils.MissingPreUpgradeVersionConfigMapError("missing configmap")

    upgrade_utils.clear_missing_post_upgrade_dataset()
    monkeypatch.setattr(upgrade_utils, "get_pre_upgrade_version_from_configmap", raise_missing)

    assert upgrade_utils.get_effective_pre_upgrade_version("post_upgrade") is None
    assert upgrade_utils.missing_post_upgrade_dataset() is True


def test_get_effective_pre_upgrade_version_raises_when_configmap_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_invalid() -> str:
        raise RuntimeError("missing pre_upgrade_version key")

    monkeypatch.setattr(upgrade_utils, "get_pre_upgrade_version_from_configmap", raise_invalid)

    with pytest.raises(RuntimeError, match="missing pre_upgrade_version key"):
        upgrade_utils.get_effective_pre_upgrade_version("post_upgrade")


def test_get_requested_upgrade_phase_returns_stored_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upgrade_utils, "REQUESTED_UPGRADE_PHASE", "post_upgrade")
    assert upgrade_utils.get_requested_upgrade_phase() == "post_upgrade"


def test_clear_missing_post_upgrade_dataset_resets_sentinel() -> None:
    upgrade_utils.mark_missing_post_upgrade_dataset()
    assert upgrade_utils.missing_post_upgrade_dataset() is True

    upgrade_utils.clear_missing_post_upgrade_dataset()
    assert upgrade_utils.missing_post_upgrade_dataset() is False
