import os
import logging

import pytest
import random
from mlflow.client import MlflowClient

from mlflow_tests.enums import ResourceType, KubeVerb
from mlflow_tests.manager.user import K8UserManager
from mlflow_tests.manager.namespace import K8Manager
from mlflow_tests.utils.client import ClientManager
from .constants.config import Config
from .shared import TestContext, UserInfo, TestData

logger = logging.getLogger(__name__)

random_gen = random.Random()


def set_user_context(user_info: tuple[str, str]) -> None:
    """Set user context for MLflow authentication.

    CRITICAL: This function sets MLflow authentication credentials in environment variables.
    These environment variables are used by the global mlflow module for authentication.

    Args:
        user_info: Tuple of (username, password/token)
            - LOCAL mode: (username, password)
            - K8s mode: ("", token) - username can be empty for token auth

    Note:
        In LOCAL mode, uses username/password authentication (Basic Auth).
        In K8s mode, uses token-based authentication (Bearer token).
    """
    username = user_info[0]
    credential = user_info[1]

    logger.info("=" * 80)
    logger.info("SETTING USER AUTHENTICATION CONTEXT")
    logger.info("=" * 80)

    if Config.LOCAL:
        # LOCAL mode: Basic authentication with username/password
        logger.info(f"Authentication mode: LOCAL (Basic Auth)")
        logger.info(f"Username: {username}")
        logger.debug("Setting MLflow authentication with username/password credentials")

        # Set up authentication context
        ClientManager.create_mlflow_client(
            username=username,
            password=credential,
            tracking_uri=Config.MLFLOW_URI
        )
        logger.info(f"Successfully set authentication context for user: {username}")

    else:
        # K8s mode: Bearer token authentication
        logger.info(f"Authentication mode: K8s (Bearer Token)")
        logger.debug(f"Token length: {len(credential) if credential else 0} characters")
        logger.debug("Setting MLflow authentication with Bearer token credentials")

        # Set up authentication context
        ClientManager.create_mlflow_client(
            token=credential,
            tracking_uri=Config.MLFLOW_URI
        )
        logger.info("Successfully set authentication context with Bearer token")

    logger.info("=" * 80)


class TestBase:
    """Base test class with common setup.

    Provides admin clients and workspace setup for all tests.
    """

    admin_client: MlflowClient
    k8_manager: K8Manager
    workspaces: list[str] = list()
    resource_map: dict[ResourceType, dict[str, list[str] | str]] = dict()
    test_context: TestContext

    @pytest.fixture(autouse=True)
    def init_clients(self, setup_clients):
        """Initialize instance attributes from session-scoped clients."""
        self.admin_client, self.k8_manager, self.user_manager, self.workspaces = setup_clients
        self.test_context = TestContext(workspaces=self.workspaces)

    @pytest.fixture(autouse=True)
    def init_experiments_and_runs(self, create_experiments_and_runs):
        """Initialize experiments and runs resource map."""
        self.resource_map = create_experiments_and_runs
        self.test_context.resource_map = self.resource_map

    @pytest.fixture(autouse=True)
    def admin_user_context(self):
        """Set admin user authentication context and create authenticated admin client.

        This fixture runs before each test to ensure the admin client has proper
        authentication credentials. It creates a NEW client with admin credentials.

        Note:
            The admin_client attribute is updated with a newly authenticated client.
            This is necessary because MLflow clients cache credentials at creation time.
        """
        logger.debug("Setting up admin user authentication context")

        if not Config.LOCAL:
            # K8s mode: Use Bearer token authentication
            logger.debug("Configuring admin authentication for K8s mode with Bearer token")
            set_user_context(("", Config.K8_API_TOKEN))
            # Create admin client after setting authentication context
            self.admin_client = ClientManager.create_mlflow_client(
                token=Config.K8_API_TOKEN,
                tracking_uri=Config.MLFLOW_URI
            )
        else:
            # LOCAL mode: Use Basic authentication
            logger.debug(f"Configuring admin authentication for LOCAL mode with username: {Config.ADMIN_USERNAME}")
            set_user_context((Config.ADMIN_USERNAME, Config.ADMIN_PASSWORD))
            # Create admin client after setting authentication context
            self.admin_client = ClientManager.create_mlflow_client(
                username=Config.ADMIN_USERNAME,
                password=Config.ADMIN_PASSWORD,
                tracking_uri=Config.MLFLOW_URI
            )

        logger.info("Admin user context configured successfully")

    @pytest.fixture(autouse=True)
    def cleanup_test_resources(self, admin_user_context):
        """Cleanup resources created during test execution with workspace awareness.

        Yields control to test, then cleans up resources tracked in test_context.
        Only cleans up test-level resources (not session/class fixtures).
        Uses admin context for cleanup to ensure proper permissions.

        Resources are cleaned up in their respective workspaces by switching
        workspace context before deletion. Original workspace is restored after cleanup.

        Error Handling:
            - Workspace switching failures are logged but don't halt cleanup
            - Non-existent workspaces are handled gracefully
            - Individual resource deletion failures are logged but don't halt cleanup
            - All errors are collected and logged as a summary
        """
        # Setup: yield control to test
        yield

        # Teardown: cleanup resources after test completes
        logger.info("Starting cleanup of test resources")

        # Store original workspace to restore later
        original_workspace = self.test_context.active_workspace

        # Track cleanup failures without failing the test
        cleanup_errors = []

        # Cleanup experiments with workspace awareness
        if self.test_context.experiments_to_delete:
            logger.info(f"Cleaning up {len(self.test_context.experiments_to_delete)} experiments")
            for experiment_id, workspace in self.test_context.experiments_to_delete.items():
                try:
                    # Switch to the correct workspace
                    if not self._switch_workspace(workspace, cleanup_errors):
                        # Workspace switch failed, skip this resource
                        continue

                    # Check if experiment exists and is not already deleted
                    experiment = self.admin_client.get_experiment(experiment_id)
                    if experiment and experiment.lifecycle_stage != "deleted":
                        self.admin_client.delete_experiment(experiment_id)
                        logger.info(f"Deleted experiment {experiment_id} in workspace {workspace}")
                    else:
                        logger.debug(f"Experiment {experiment_id} already deleted or not found")
                except Exception as e:
                    # Log error but continue cleanup
                    error_msg = f"Failed to delete experiment {experiment_id} in workspace {workspace}: {e}"
                    logger.warning(error_msg)
                    cleanup_errors.append(error_msg)

        # Cleanup runs with workspace awareness
        if self.test_context.runs_to_delete:
            logger.info(f"Cleaning up {len(self.test_context.runs_to_delete)} runs")
            for run_id, workspace in self.test_context.runs_to_delete.items():
                try:
                    # Switch to the correct workspace
                    if not self._switch_workspace(workspace, cleanup_errors):
                        # Workspace switch failed, skip this resource
                        continue

                    # Check if run exists
                    run = self.admin_client.get_run(run_id)
                    if run and run.info.lifecycle_stage != "deleted":
                        self.admin_client.delete_run(run_id)
                        logger.info(f"Deleted run {run_id} in workspace {workspace}")
                    else:
                        logger.debug(f"Run {run_id} already deleted or not found")
                except Exception as e:
                    error_msg = f"Failed to delete run {run_id} in workspace {workspace}: {e}"
                    logger.warning(error_msg)
                    cleanup_errors.append(error_msg)

        # Cleanup registered models with workspace awareness
        if self.test_context.models_to_delete:
            logger.info(f"Cleaning up {len(self.test_context.models_to_delete)} registered models")
            for model_name, workspace in self.test_context.models_to_delete.items():
                try:
                    # Switch to the correct workspace
                    if not self._switch_workspace(workspace, cleanup_errors):
                        # Workspace switch failed, skip this resource
                        continue

                    try:
                        model = self.admin_client.get_registered_model(model_name)
                        if model:
                            self.admin_client.delete_registered_model(model_name)
                            logger.info(f"Deleted registered model {model_name} in workspace {workspace}")
                    except Exception as get_error:
                        # Model may not exist (already deleted or never created)
                        if "RESOURCE_DOES_NOT_EXIST" in str(get_error) or "does not exist" in str(get_error).lower():
                            logger.debug(f"Registered model {model_name} already deleted or not found")
                        else:
                            error_msg = f"Failed to check registered model {model_name} in workspace {workspace}: {get_error}"
                            logger.warning(error_msg)
                            cleanup_errors.append(error_msg)
                except Exception as e:
                    error_msg = f"Failed to delete registered model {model_name} in workspace {workspace}: {e}"
                    logger.warning(error_msg)
                    cleanup_errors.append(error_msg)

        # Cleanup users (users are global, no workspace switching needed)
        if self.test_context.users_to_delete:
            logger.info(f"Cleaning up {len(self.test_context.users_to_delete)} users")
            for user_info in self.test_context.users_to_delete:
                try:
                    # Attempt to delete user
                    # Pass workspace/namespace if available (needed for K8s, ignored for MLflow)
                    self.user_manager.delete_user(
                        username=user_info.uname,
                        namespace=user_info.workspace
                    )
                    logger.info(f"Deleted user: {user_info.uname}")
                except Exception as e:
                    error_msg = f"Failed to delete user {user_info.uname}: {e}"
                    logger.warning(error_msg)
                    cleanup_errors.append(error_msg)

        # Restore original workspace if it was set
        if original_workspace:
            try:
                import mlflow
                mlflow.set_workspace(original_workspace)
                logger.debug(f"Restored original workspace: {original_workspace}")
            except Exception as e:
                logger.warning(f"Failed to restore original workspace {original_workspace}: {e}")

        # Log cleanup summary
        if cleanup_errors:
            logger.warning(f"Cleanup completed with {len(cleanup_errors)} errors:")
            for error in cleanup_errors:
                logger.warning(f"  - {error}")
        else:
            logger.info("Cleanup completed successfully")

    def _switch_workspace(self, workspace: str, cleanup_errors: list[str]) -> bool:
        """Switch to a specified workspace with error handling.

        Args:
            workspace: Target workspace name
            cleanup_errors: List to append errors to

        Returns:
            bool: True if workspace switch succeeded, False otherwise

        Note:
            This is a helper method for workspace-aware cleanup operations.
            Errors are logged and added to cleanup_errors but don't raise exceptions.
        """
        try:
            # Validate workspace exists in known workspaces
            if workspace not in self.test_context.workspaces:
                error_msg = f"Workspace {workspace} not found in available workspaces: {self.test_context.workspaces}"
                logger.warning(error_msg)
                cleanup_errors.append(error_msg)
                return False

            # Attempt workspace switch
            import mlflow
            mlflow.set_workspace(workspace)
            logger.debug(f"Switched to workspace: {workspace}")
            return True

        except AttributeError as e:
            # mlflow.set_workspace may not exist in all MLflow versions
            error_msg = f"Workspace switching not supported by this MLflow version: {e}"
            logger.warning(error_msg)
            cleanup_errors.append(error_msg)
            return False

        except Exception as e:
            error_msg = f"Failed to switch to workspace {workspace}: {e}"
            logger.warning(error_msg)
            cleanup_errors.append(error_msg)
            return False

    @pytest.fixture(scope="function", autouse=False)
    def create_user_with_permissions(self):
        """Create a test user with specific permissions in a workspace.

        Returns a function that creates users with role-based permissions and
        returns an authenticated MLflow client for that user.
        """
        def _create_user(workspace: str, verbs: list[KubeVerb], resource_types: list[ResourceType], subresources: list[str]=None):
            """Internal function to create user with permissions and authenticated client.

            Args:
                workspace: Workspace/namespace for the user
                verbs: List of Kubernetes verbs to grant
                resource_types: List of resource types to grant access to
                subresources: Optional list of subresources

            Returns:
                UserInfo object with user credentials and workspace

            Note:
                This function sets up the authentication context for the created user.
            """
            username = f"test-user-{random_gen.randint(0,10_000)}"
            logger.info(f"Creating test user '{username}' in workspace '{workspace}'")
            verb_names = [verb.value for verb in verbs] if isinstance(verbs, list) else [verbs.value]
            resource_names = [rt.value for rt in resource_types]
            logger.debug(f"User will have {verb_names} verbs on {resource_names}")

            # Create the user
            user_info = self.user_manager.create_user(username=username, namespace=workspace)
            logger.info(f"Created user '{username}' in workspace '{workspace}'")
            logger.debug(f"User credentials: username={user_info[0]}, credential_length={len(user_info[1])}")

            # Validate user token for K8s mode
            if not Config.LOCAL:
                token = user_info[1]
                if not token or len(token) < 50:  # K8s tokens are typically much longer
                    logger.error(f"Invalid or short token for user '{username}': length={len(token) if token else 0}")
                    raise ValueError(f"User creation failed - token too short for K8s authentication")
                logger.info(f"User '{username}' token validation passed (K8s mode)")

            # Create role and permissions
            logger.debug(f"Assigning {verb_names} verbs on {resource_names} to user '{username}'")
            self.user_manager.create_role(
                name=username,
                workspace_name=workspace,
                verbs=verbs if isinstance(verbs, list) else [verbs],
                resources=resource_types,
                subresources=subresources
            )
            logger.info(f"Assigned {verb_names} permissions on {resource_names} to user '{username}'")

            # Set authentication context
            logger.debug(f"Setting authentication context for user '{username}'")
            set_user_context(user_info=user_info)
            logger.info(f"Authentication context set for user '{username}'")

            # Create UserInfo object
            user_info_obj = UserInfo(
                uname=user_info[0],
                upass=user_info[1],
                workspace=workspace,
                resource_types=resource_types,
                verbs=verbs if isinstance(verbs, list) else [verbs],
                subresources=subresources
            )

            # Add user to cleanup list
            self.test_context.add_user_for_cleanup(user_info_obj)
            logger.debug(f"Added user '{username}' to cleanup list")

            return user_info_obj
        return _create_user

    def _execute_test_steps(self, test_data: TestData) -> None:
        """Execute test steps for the test.

        Iterates over test_steps and executes actions and validations for each step.
        Actions may fail (especially in negative tests), but validations should confirm
        whether the failure was expected or not.

        Args:
            test_data: Test configuration containing test steps to execute.
        """
        if not hasattr(test_data, 'test_steps') or not test_data.test_steps:
            logger.debug("No test steps to execute")
            return

        # Handle both single TestStep and list of TestSteps
        test_steps = test_data.test_steps if isinstance(test_data.test_steps, list) else [test_data.test_steps]
        logger.info(f"Executing {len(test_steps)} test step(s)")

        for i, step in enumerate(test_steps, 1):
            logger.info(f"--- Test Step {i} ---")

            # Execute action if present (don't stop on failure - validation will check if it was expected)
            if step.action_func:
                self._execute_action(step.action_func, i)

            # Execute validation if present
            if step.validate_func:
                self._execute_validation(step.validate_func, i)

    def _execute_action(self, action_func, step_number: int) -> None:
        """Execute a single action function.

        Actions may succeed or fail. Failures are stored in test_context.last_error
        for validation functions to verify if the failure was expected.

        Args:
            action_func: The action function to execute.
            step_number: The step number for logging.
        """
        action_name = action_func.__name__
        logger.info(f"Step {step_number}: Executing action '{action_name}'")

        try:
            action_func(self.test_context)
            logger.info(f"Action '{action_name}' completed without exception")
        except Exception as e:
            # Store exception for validation to verify expected failures
            logger.warning(f"Action '{action_name}' raised exception: {type(e).__name__}: {e}")
            self.test_context.last_error = e

    def _execute_validation(self, validate_func, step_number: int) -> None:
        """Execute a single validation function.

        Args:
            validate_func: The validation function to execute.
            step_number: The step number for logging.

        Raises:
            AssertionError: If validation fails.
        """
        validation_name = validate_func.__name__
        logger.info(f"Step {step_number}: Executing validation '{validation_name}'")

        try:
            validate_func(self.test_context)
            logger.info(f"Validation '{validation_name}' passed successfully")
        except AssertionError as e:
            logger.error(f"Validation '{validation_name}' failed: {e}")
            raise