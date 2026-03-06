"""Workspace action functions.

These actions exercise the MLflow Kubernetes workspace discovery API.
"""

import logging

import requests

from ..constants.config import Config
from ..shared import TestContext

logger = logging.getLogger(__name__)


def _extract_names(obj) -> set[str]:
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("workspaces") or obj.get("items") or obj.get("namespaces") or []
    else:
        items = []

    out: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        if isinstance(name, str):
            out.add(name)
            continue

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            meta_name = metadata.get("name")
            if isinstance(meta_name, str):
                out.add(meta_name)

    return out


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
