"""Shared helpers for upgrade-phase MLflow pytest selection and state handoff."""

from __future__ import annotations

import logging
from pathlib import Path

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from packaging.version import Version

from mlflow_tests.utils.client import ClientManager
from .constants.config import Config

logger = logging.getLogger(__name__)

UPGRADE_PHASES = {"pre_upgrade", "post_upgrade"}
REQUESTED_UPGRADE_PHASE = ""


def set_requested_upgrade_phase(phase: str) -> None:
    """Store the upgrade phase inferred from explicit pytest marker selection."""
    global REQUESTED_UPGRADE_PHASE
    REQUESTED_UPGRADE_PHASE = phase.strip()


def get_requested_upgrade_phase() -> str:
    """Return the currently requested upgrade phase, if any."""
    return REQUESTED_UPGRADE_PHASE


def is_upgrade_phase(phase: str | None = None) -> bool:
    """Return whether the requested or configured phase is an upgrade phase."""
    candidate = (phase if phase is not None else REQUESTED_UPGRADE_PHASE).strip()
    return candidate in UPGRADE_PHASES


def normalize_mlflow_version(version: str) -> str:
    """Normalize an MLflow version string to ``major.minor``."""
    cleaned = (version or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    parsed = Version(cleaned)
    return f"{parsed.major}.{parsed.minor}"


def parse_minimum_version_from_path(path: str | Path) -> tuple[int, int] | None:
    """Extract the minimum supported version from a versioned test file path."""
    file_name = Path(path).stem
    parts = file_name.split("_")
    if len(parts) != 3 or parts[0] != "test":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def get_upgrade_workspace() -> str:
    """Return the static workspace namespace used by upgrade phases."""
    if not Config.UPGRADE_WORKSPACE:
        raise ValueError("upgrade_workspace must be configured for upgrade phases")
    return Config.UPGRADE_WORKSPACE


def get_supported_upgrade_version_from_env() -> str:
    """Read the pre-upgrade supported MLflow version from the configured env var."""
    version = Config.UPGRADE_SUPPORTED_VERSION
    if not version:
        raise ValueError(
            "Upgrade test phase requires the Dockerfile-derived MLflow version environment "
            "variable 'MLFLOW_TEST_SUPPORTED_VERSION' to be set"
        )
    return normalize_mlflow_version(version)


def upsert_pre_upgrade_version_configmap(version: str) -> None:
    """Create or update the namespace-scoped ConfigMap used by post-upgrade selection."""
    normalized = normalize_mlflow_version(version)
    namespace = Config.UPGRADE_CONFIGMAP_NAMESPACE or get_upgrade_workspace()
    body = k8s_client.V1ConfigMap(
        metadata=k8s_client.V1ObjectMeta(
            name=Config.UPGRADE_VERSION_CONFIGMAP_NAME,
            namespace=namespace,
        ),
        data={Config.UPGRADE_VERSION_CONFIGMAP_KEY: normalized},
    )
    core_v1_api, _ = ClientManager.create_k8s_client()
    try:
        core_v1_api.create_namespaced_config_map(namespace=namespace, body=body)
        logger.info(
            "Created upgrade version ConfigMap '%s' in namespace '%s' with version %s",
            Config.UPGRADE_VERSION_CONFIGMAP_NAME,
            namespace,
            normalized,
        )
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_v1_api.patch_namespaced_config_map(
            name=Config.UPGRADE_VERSION_CONFIGMAP_NAME,
            namespace=namespace,
            body={"data": {Config.UPGRADE_VERSION_CONFIGMAP_KEY: normalized}},
        )
        logger.info(
            "Patched upgrade version ConfigMap '%s' in namespace '%s' with version %s",
            Config.UPGRADE_VERSION_CONFIGMAP_NAME,
            namespace,
            normalized,
        )


def get_pre_upgrade_version_from_configmap() -> str:
    """Read the stored pre-upgrade version from the namespace-scoped ConfigMap."""
    namespace = Config.UPGRADE_CONFIGMAP_NAMESPACE or get_upgrade_workspace()
    core_v1_api, _ = ClientManager.create_k8s_client()
    try:
        config_map = core_v1_api.read_namespaced_config_map(
            name=Config.UPGRADE_VERSION_CONFIGMAP_NAME,
            namespace=namespace,
        )
    except ApiException as exc:
        if exc.status == 404:
            raise RuntimeError(
                "Post-upgrade selection requires ConfigMap "
                f"'{Config.UPGRADE_VERSION_CONFIGMAP_NAME}' in namespace '{namespace}'"
            ) from exc
        raise

    value = (config_map.data or {}).get(Config.UPGRADE_VERSION_CONFIGMAP_KEY, "").strip()
    if not value:
        raise RuntimeError(
            "Post-upgrade selection requires key "
            f"'{Config.UPGRADE_VERSION_CONFIGMAP_KEY}' in ConfigMap "
            f"'{Config.UPGRADE_VERSION_CONFIGMAP_NAME}'"
        )
    return normalize_mlflow_version(value)


def get_effective_pre_upgrade_version(phase: str | None = None) -> str | None:
    """Return the version used to gate versioned upgrade test files."""
    effective_phase = (phase if phase is not None else REQUESTED_UPGRADE_PHASE).strip()
    if effective_phase == "pre_upgrade":
        return get_supported_upgrade_version_from_env()
    if effective_phase == "post_upgrade":
        try:
            return get_pre_upgrade_version_from_configmap()
        except RuntimeError:
            # This graceful "no matching post-upgrade tests" path exists because
            # RHOAI 3.3 shipped MLflow as Dev Preview, so we do not have
            # pre-upgrade coverage or a handoff ConfigMap for that source version.
            logger.info(
                "Skipping version-gated post-upgrade tests because the pre-upgrade version "
                "ConfigMap is not present."
            )
            return None
    raise ValueError(f"Unsupported upgrade phase '{effective_phase}'")


def should_run_versioned_test(path: str | Path, phase: str | None = None) -> bool:
    """Return whether the versioned test file is active for the effective pre-upgrade version."""
    minimum = parse_minimum_version_from_path(path)
    if minimum is None:
        return True
    effective_version = get_effective_pre_upgrade_version(phase)
    if effective_version is None:
        return False
    effective = Version(effective_version)
    return effective >= Version(f"{minimum[0]}.{minimum[1]}")
