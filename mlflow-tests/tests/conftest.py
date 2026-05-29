"""Pytest configuration and fixtures."""

import logging
import os
from pathlib import Path

import pytest
import random

from mlflow_tests.enums import ResourceType, KubeVerb
from mlflow_tests.manager.namespace import K8Manager
from mlflow_tests.manager.user import K8UserManager
from mlflow_tests.utils.client import ClientManager
from .mark_expression_utils import InvalidUpgradePhaseSelection, infer_requested_upgrade_phase
from .constants.config import Config
from .upgrade_utils import (
    UPGRADE_PHASES,
    get_upgrade_workspace,
    is_upgrade_phase,
    set_requested_upgrade_phase,
    should_run_versioned_test,
)

logger = logging.getLogger(__name__)
random_gen = random.Random()


def _get_requested_upgrade_phase(mark_expression: str | None = None) -> str:
    """Return the effective upgrade phase from explicit pytest marker selection."""
    try:
        return infer_requested_upgrade_phase(mark_expression)
    except InvalidUpgradePhaseSelection as exc:
        raise pytest.UsageError(str(exc)) from exc


def _should_ignore_upgrade_collection(collection_path: Path, phase: str) -> bool:
    """Return whether pytest should skip collecting a path for upgrade selection."""
    path_name = collection_path.name
    parent_name = collection_path.parent.name

    if phase in UPGRADE_PHASES:
        allowed_dir = "pre_upgrade" if phase == "pre_upgrade" else "post_upgrade"
        if path_name in UPGRADE_PHASES:
            return path_name != allowed_dir
        if parent_name in UPGRADE_PHASES:
            return parent_name != allowed_dir
        return path_name.startswith("test_")

    return path_name in UPGRADE_PHASES or parent_name in UPGRADE_PHASES


def pytest_configure(config):
    """Infer the upgrade phase from explicit marker selection when env is unset."""
    set_requested_upgrade_phase(_get_requested_upgrade_phase(config.option.markexpr))


def _get_configured_workspaces() -> list[str]:
    """Return the workspaces for the current pytest phase."""
    if is_upgrade_phase():
        return [get_upgrade_workspace()]
    return Config.WORKSPACES


def pytest_ignore_collect(collection_path, config):
    """Keep upgrade-only modules opt-in at collection time."""
    phase = _get_requested_upgrade_phase(config.option.markexpr)
    should_ignore = _should_ignore_upgrade_collection(collection_path, phase)
    return True if should_ignore else None


def pytest_collection_modifyitems(config, items):
    """Keep upgrade tests opt-in and gate versioned files per upgrade phase."""
    phase = _get_requested_upgrade_phase(config.option.markexpr)
    deselected = []
    selected = []

    for item in items:
        marker_names = {marker.name for marker in item.iter_markers()}
        is_upgrade_item = bool(marker_names & UPGRADE_PHASES)

        if not phase:
            if is_upgrade_item:
                deselected.append(item)
            else:
                selected.append(item)
            continue

        if not is_upgrade_item:
            deselected.append(item)
            continue

        if phase not in marker_names:
            deselected.append(item)
            continue

        try:
            should_run = should_run_versioned_test(item.path, phase)
        except Exception as exc:
            guidance = (
                "For direct pre-upgrade pytest runs, set `MLFLOW_TEST_SUPPORTED_VERSION`, "
                "or run the suite through `images/test-run.sh`."
            )
            if phase == "post_upgrade":
                guidance = (
                    "For direct post-upgrade pytest runs, run the suite through "
                    "`images/test-run.sh` after the pre-upgrade phase has written the ConfigMap."
                )
            raise pytest.UsageError(
                "Unable to resolve upgrade test version gating for "
                f"'{item.path}': {exc}. {guidance}"
            ) from exc

        if not should_run:
            deselected.append(item)
            continue

        selected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected

@pytest.fixture(scope="session")
def setup_clients():
    """Session-scoped fixture to set up clients and managers.

    Returns:
        tuple: (admin_client, k8_manager, user_manager, workspaces)
    """
    logger.info("=" * 80)
    logger.info("STARTING TEST SESSION SETUP")
    logger.info("=" * 80)

    # Disable SSL verification for testing environments
    # WARNING: This is insecure and should only be used in development/testing
    import urllib3
    logger.warning("Disabling SSL verification for testing environment - THIS IS INSECURE")
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Set environment variables to disable SSL verification
    os.environ['MLFLOW_TRACKING_INSECURE_TLS'] = Config.DISABLE_TLS
    os.environ['CURL_CA_BUNDLE'] = ''
    os.environ['REQUESTS_CA_BUNDLE'] = ''
    logger.debug("Set SSL environment variables for insecure testing")
    if Config.ARTIFACT_STORAGE == "s3" and not Config.SERVE_ARTIFACTS:
        logger.debug("Set AWS Credentials because artifact store is s3 and server_artifacts=false")
        if not Config.AWS_ACCESS_KEY or not Config.AWS_SECRET_KEY:
            raise RuntimeError("AWS Credentials not set, please set env variables 'AWS_ACCESS_KEY_ID' & 'AWS_SECRET_ACCESS_KEY'")
        os.environ['MLFLOW_S3_ENDPOINT_URL'] = Config.S3_URL
        os.environ['AWS_ACCESS_KEY_ID'] = Config.AWS_ACCESS_KEY
        os.environ['AWS_SECRET_ACCESS_KEY'] = Config.AWS_SECRET_KEY

    # Define workspaces
    workspaces = _get_configured_workspaces()
    logger.info(f"Configured workspaces: {workspaces}")

    logger.info("Setting up Kubernetes environment")
    core_v1_api, rbac_v1_api = ClientManager.create_k8s_client()
    logger.debug("Created Kubernetes API clients")

    # Create K8 managers
    k8_manager = K8Manager(core_v1_api)
    user_manager = K8UserManager(core_v1_api, rbac_v1_api)
    logger.info("Created K8 managers")

    # Create test namespaces
    logger.info(f"Creating {len(workspaces)} test namespaces")
    for workspace in workspaces:
        logger.debug(f"Creating namespace: {workspace}")
        k8_manager.create_namespace(workspace)
        k8_manager.label_namespace(workspace, {"mlflow-enabled": "true"})
    logger.info("Successfully created all test namespaces")

    admin_client = ClientManager.create_mlflow_client(
        token=Config.K8_API_TOKEN,
        tracking_uri=Config.MLFLOW_URI
    )
    logger.info(f"Created MLflow admin client for K8s environment (URI: {Config.MLFLOW_URI})")

    logger.info("Test session setup completed successfully")
    logger.info("=" * 80)
    return admin_client, k8_manager, user_manager, workspaces

@pytest.fixture(autouse=True, scope="function")
def cleanup_active_runs():
    """Ensure no MLflow runs are active before and after each test.

    This fixture automatically runs before and after each test to prevent
    "already active run" errors by ending any active runs that weren't
    properly cleaned up. Also disables autologging to prevent automatic
    run creation.
    """
    import mlflow

    def _end_active_runs(when):
        """Helper to end any active runs."""
        try:
            # Check if there are any active runs
            active_run = mlflow.active_run()
            if active_run:
                logger.warning(f"Found active run {when} test: {active_run.info.run_id}")
                mlflow.end_run()
                logger.info(f"Ended active run {when} test: {active_run.info.run_id}")

                # Double check - sometimes nested runs exist
                while mlflow.active_run():
                    nested_run = mlflow.active_run()
                    logger.warning(f"Found nested active run: {nested_run.info.run_id}")
                    mlflow.end_run()
                    logger.info(f"Ended nested active run: {nested_run.info.run_id}")
            else:
                logger.debug(f"No active runs found {when} test")
        except Exception as e:
            logger.warning(f"Error while checking/ending active runs {when} test: {e}")

    def _disable_autologging():
        """Disable all MLflow autologging to prevent automatic run creation."""
        try:
            # Disable all autologging
            mlflow.autolog(disable=True)
            logger.debug("Disabled MLflow autologging")

            # Also disable specific framework autologging if any are enabled
            frameworks = ['sklearn', 'tensorflow', 'pytorch', 'keras', 'lightgbm', 'xgboost']
            for framework in frameworks:
                try:
                    if hasattr(mlflow, framework):
                        getattr(mlflow, framework).autolog(disable=True)
                        logger.debug(f"Disabled {framework} autologging")
                except Exception:
                    # Framework may not be installed or autologging not supported
                    pass
        except Exception as e:
            logger.warning(f"Error while disabling autologging: {e}")

    # Before test: disable autologging and ensure no active runs
    _disable_autologging()
    _end_active_runs("before")

    # Run the test
    yield

    # After test: cleanup any active runs and disable autologging again
    _end_active_runs("after")
    _disable_autologging()


@pytest.fixture(autouse=True, scope="session")
def create_experiments_and_runs(setup_clients):
    """Create session-scoped test resources for all workspaces.

    This fixture runs once per test session and creates baseline resources
    (experiments, runs, registered models) that tests can use for validation
    and permission checks.

    Note:
        This fixture uses the admin_client credentials that were set during
        setup_clients. The mlflow module will use those credentials since they
        are set in the environment variables.
    """
    import mlflow

    logger.info("=" * 80)
    logger.info("CREATING SESSION-SCOPED TEST RESOURCES")
    logger.info("=" * 80)

    admin_client, k8_manager, user_manager, workspaces = setup_clients

    if is_upgrade_phase():
        logger.info("Skipping baseline resource creation for upgrade-only pytest phase")
        logger.info("=" * 80)
        return {}

    resource_map = dict()

    # Verify admin authentication is properly set
    logger.debug("Verifying admin authentication credentials are set")
    if not os.environ.get('MLFLOW_TRACKING_TOKEN'):
        logger.warning("MLFLOW_TRACKING_TOKEN not set - admin client may not be authenticated")

    logger.info(f"Creating baseline resources for {len(workspaces)} workspaces")

    for workspace in workspaces:
        logger.info(f"Processing workspace: {workspace}")
        mlflow.set_workspace(workspace)
        logger.debug(f"Set active workspace to: {workspace}")

        experiment_resources = {}
        for slot in ("primary", "secondary"):
            experiment_name = f"test-experiment-{slot}-{random_gen.randint(1, 10000)}"
            logger.debug(f"Creating {slot} baseline experiment: {experiment_name}")

            try:
                experiment_id = mlflow.create_experiment(experiment_name)
                logger.info(
                    "Created %s experiment '%s' with ID: %s in workspace '%s'",
                    slot,
                    experiment_name,
                    experiment_id,
                    workspace,
                )
            except Exception as e:
                logger.error(f"Failed to create baseline experiment in workspace '{workspace}': {e}")
                logger.error("Authentication may not be properly configured. Check credentials.")
                raise

            experiment_resources[slot] = {
                "id": experiment_id,
                "name": experiment_name,
            }

        resource_map.setdefault(ResourceType.EXPERIMENTS, {})[workspace] = experiment_resources
        logger.debug(f"Added experiment resources to resource map for workspace '{workspace}'")

        model_resources = {}
        for slot in ("primary", "secondary"):
            model_name = f"test-model-{slot}-{random_gen.randint(1, 10000)}"
            logger.debug(f"Creating {slot} baseline registered model: {model_name}")

            try:
                model = admin_client.create_registered_model(model_name)
                logger.info(
                    "Created %s registered model '%s' in workspace '%s'",
                    slot,
                    model_name,
                    workspace,
                )
            except Exception as e:
                logger.error(f"Failed to create baseline registered model in workspace '{workspace}': {e}")
                logger.error("Authentication may not be properly configured. Check credentials.")
                raise

            model_resources[slot] = {"name": model.name}

        resource_map.setdefault(ResourceType.REGISTERED_MODELS, {})[workspace] = model_resources
        logger.debug(f"Added registered model resources to resource map for workspace '{workspace}'")

    logger.info(f"Successfully created all baseline resources")
    logger.info(f"Resource summary - Experiments: {len(resource_map.get(ResourceType.EXPERIMENTS, {}))}, "
               f"Registered Models: {len(resource_map.get(ResourceType.REGISTERED_MODELS, {}))}")
    logger.info("=" * 80)
    return resource_map
