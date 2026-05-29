from pathlib import Path

import pytest

from . import conftest as tests_conftest


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_get_requested_upgrade_phase_extracts_single_phase() -> None:
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade and smoke") == "pre_upgrade"
    assert tests_conftest._get_requested_upgrade_phase("post_upgrade") == "post_upgrade"


def test_get_requested_upgrade_phase_rejects_multiple_phases() -> None:
    with pytest.raises(pytest.UsageError, match="target only one upgrade phase"):
        tests_conftest._get_requested_upgrade_phase("pre_upgrade or post_upgrade")


def test_get_requested_upgrade_phase_ignores_negated_or_nonexclusive_phases() -> None:
    assert tests_conftest._get_requested_upgrade_phase("not pre_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("not post_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("not pre_upgrade and not post_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade or smoke") == ""


def test_should_ignore_upgrade_collection_skips_upgrade_modules_during_normal_runs() -> None:
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/pre_upgrade/test_3_10.py"),
        "",
    )
    assert not tests_conftest._should_ignore_upgrade_collection(
        Path("tests/test_experiments.py"),
        "",
    )


def test_should_ignore_upgrade_collection_keeps_only_requested_phase() -> None:
    assert not tests_conftest._should_ignore_upgrade_collection(
        Path("tests/pre_upgrade/test_3_10.py"),
        "pre_upgrade",
    )
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/post_upgrade/test_3_10.py"),
        "pre_upgrade",
    )
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/test_experiments.py"),
        "pre_upgrade",
    )
