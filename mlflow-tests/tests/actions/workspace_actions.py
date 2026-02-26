"""Workspace action functions.

These actions exercise the MLflow Kubernetes workspace discovery API.
"""

import logging
import os

import requests

from ..constants.config import Config
from ..shared import TestContext

logger = logging.getLogger(__name__)


def _extract_names(obj) -> set[str]:
    if obj is None:
        return set()
    if isinstance(obj, str):
        return {obj}
    if isinstance(obj, list):
        out: set[str] = set()
        for item in obj:
            out |= _extract_names(item)
        return out
    if isinstance(obj, dict):
        out: set[str] = set()
        for key in ("workspaces", "items", "namespaces"):
            if key in obj:
                out |= _extract_names(obj[key])
        if "name" in obj and isinstance(obj["name"], str):
            out.add(obj["name"])
        for v in obj.values():
            out |= _extract_names(v)
        return out
    return set()


def action_create_unlabeled_namespace(test_context: TestContext) -> None:
    """Create a namespace without the workspace label."""
    if test_context.k8_manager is None:
        raise RuntimeError("test_context.k8_manager is not set")

    namespace = f"unlabeled-workspace-{os.getpid()}"
    logger.info(f"Creating unlabeled namespace: {namespace}")
    test_context.k8_manager.create_namespace(namespace)
    test_context.unlabeled_namespace = namespace


def action_list_workspaces(test_context: TestContext) -> None:
    """Call the workspaces endpoint and store parsed names in context."""
    url = f"{Config.MLFLOW_URI.rstrip('/')}/mlflow/ajax-api/3.0/mlflow/workspaces"
    headers = {"Authorization": f"Bearer {Config.K8_API_TOKEN}"}

    logger.info(f"Listing workspaces via {url}")
    resp = requests.get(url, headers=headers, verify=False, timeout=30)
    if resp.status_code != 200:
        raise AssertionError(f"GET {url} -> {resp.status_code}: {resp.text}")

    payload = resp.json()
    test_context.discovered_workspaces = _extract_names(payload)


def action_delete_unlabeled_namespace(test_context: TestContext) -> None:
    """Best-effort cleanup of the unlabeled namespace created for this test."""
    if test_context.k8_manager is None:
        return
    if not test_context.unlabeled_namespace:
        return

    try:
        test_context.k8_manager.delete_namespace(test_context.unlabeled_namespace)
    except Exception as e:
        logger.warning(f"Failed to delete namespace {test_context.unlabeled_namespace}: {e}")

