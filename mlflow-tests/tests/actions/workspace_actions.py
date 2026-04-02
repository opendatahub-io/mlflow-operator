"""Workspace action functions.

These actions exercise the MLflow Kubernetes workspace discovery API.
"""

import logging

import requests

from ..constants.config import Config
from ..shared import TestContext

logger = logging.getLogger(__name__)


def _extract_names(payload: dict) -> set[str]:
    return {ws["name"] for ws in payload.get("workspaces", [])}


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
