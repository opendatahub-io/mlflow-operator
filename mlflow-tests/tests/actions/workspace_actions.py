"""Workspace action functions.

These actions exercise the MLflow Kubernetes workspace discovery API.
"""

import logging
import random
import string

import requests

from ..constants.config import Config
from ..shared import TestContext

logger = logging.getLogger(__name__)
random_gen = random.Random()


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

        name = obj.get("name")
        if isinstance(name, str):
            out.add(name)

        metadata = obj.get("metadata")
        if isinstance(metadata, dict):
            meta_name = metadata.get("name")
            if isinstance(meta_name, str):
                out.add(meta_name)
        return out
    return set()


def action_create_unlabeled_namespace(test_context: TestContext) -> None:
    """Create a namespace without the workspace label."""
    if test_context.k8_manager is None:
        raise RuntimeError("test_context.k8_manager is not set")

    random_suffix = "".join(random_gen.choices(string.ascii_lowercase + string.digits, k=8))
    namespace = f"unlabeled-workspace-{random_suffix}"
    logger.info(f"Creating unlabeled namespace: {namespace}")
    test_context.k8_manager.create_namespace(namespace)
    test_context.unlabeled_namespace = namespace
    test_context.add_namespace_for_cleanup(namespace)


def action_list_workspaces(test_context: TestContext) -> None:
    """Call the workspaces endpoint and store parsed names in context."""
    url = f"{Config.MLFLOW_URI.rstrip('/')}/mlflow/ajax-api/3.0/mlflow/workspaces"
    headers = {"Authorization": f"Bearer {Config.K8_API_TOKEN}"}

    logger.info(f"Listing workspaces via {url}")
    verify: bool | str = True
    if str(Config.DISABLE_TLS).lower() == "true":
        verify = False
    elif Config.CA_BUNDLE:
        verify = Config.CA_BUNDLE

    resp = requests.get(url, headers=headers, verify=verify, timeout=30)
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
        test_context.namespaces_to_delete.discard(test_context.unlabeled_namespace)
    except Exception as e:
        logger.warning(f"Failed to delete namespace {test_context.unlabeled_namespace}: {e}")

