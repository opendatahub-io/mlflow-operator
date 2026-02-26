"""Workspace validation functions."""

import logging

from ..shared import TestContext

logger = logging.getLogger(__name__)


def validate_workspaces_filtered(test_context: TestContext) -> None:
    """Validate that labeled workspaces appear and unlabeled does not."""
    names = test_context.discovered_workspaces

    logger.info(f"Discovered workspaces: {sorted(names)}")

    for ws in test_context.workspaces:
        assert ws in names, f"expected labeled workspace {ws!r} in discovered workspaces: {sorted(names)}"

    assert test_context.unlabeled_namespace, "unlabeled_namespace was not set"
    assert (
        test_context.unlabeled_namespace not in names
    ), f"unexpected unlabeled namespace {test_context.unlabeled_namespace!r} in discovered workspaces: {sorted(names)}"

