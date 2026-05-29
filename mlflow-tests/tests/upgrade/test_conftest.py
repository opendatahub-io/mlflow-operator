from pathlib import Path
from types import SimpleNamespace

import pytest

from .. import conftest as tests_conftest


@pytest.fixture(scope="module", autouse=True)
def create_experiments_and_runs() -> dict:
    """Override the integration bootstrap fixture for helper-level tests."""
    return {}


def test_get_requested_upgrade_phase_extracts_single_phase() -> None:
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade") == "pre_upgrade"
    assert tests_conftest._get_requested_upgrade_phase("post_upgrade") == "post_upgrade"


def test_get_requested_upgrade_phase_treats_non_exact_expressions_as_normal_runs() -> None:
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade and smoke") == ""
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade or post_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("not pre_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("not post_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("not pre_upgrade and not post_upgrade") == ""
    assert tests_conftest._get_requested_upgrade_phase("pre_upgrade or smoke") == ""


def test_should_ignore_upgrade_collection_skips_upgrade_modules_during_normal_runs() -> None:
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/upgrade/pre_upgrade/test_3_10.py"),
        "",
    )
    assert not tests_conftest._should_ignore_upgrade_collection(
        Path("tests/test_experiments.py"),
        "",
    )


def test_should_ignore_upgrade_collection_keeps_only_requested_phase() -> None:
    assert not tests_conftest._should_ignore_upgrade_collection(
        Path("tests/upgrade/pre_upgrade/test_3_10.py"),
        "pre_upgrade",
    )
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/upgrade/post_upgrade/test_3_10.py"),
        "pre_upgrade",
    )
    assert tests_conftest._should_ignore_upgrade_collection(
        Path("tests/test_experiments.py"),
        "pre_upgrade",
    )


def test_pytest_sessionfinish_allows_missing_post_upgrade_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tests_conftest, "missing_post_upgrade_dataset", lambda: True)

    session = SimpleNamespace(
        config=SimpleNamespace(
            option=SimpleNamespace(markexpr="post_upgrade"),
            pluginmanager=SimpleNamespace(getplugin=lambda name: None),
        ),
        exitstatus=5,
    )

    tests_conftest.pytest_sessionfinish(session, 5)

    assert session.exitstatus == 0


def test_pytest_sessionfinish_leaves_other_empty_runs_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tests_conftest, "missing_post_upgrade_dataset", lambda: False)

    session = SimpleNamespace(
        config=SimpleNamespace(
            option=SimpleNamespace(markexpr="post_upgrade"),
            pluginmanager=SimpleNamespace(getplugin=lambda name: None),
        ),
        exitstatus=5,
    )

    tests_conftest.pytest_sessionfinish(session, 5)

    assert session.exitstatus == 5
