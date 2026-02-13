from mlflow_tests.enums import ResourceType
from .user_info import UserInfo
from .error_models import ErrorResponse
from dataclasses import dataclass, field
from typing import Optional, Any
import logging
from mlflow import MlflowClient

logger = logging.getLogger(__name__)


@dataclass
class TestContext:
    """Context for tracking test resources and state with workspace awareness.

    Manages workspaces, experiments, runs, models, and users created during tests
    to facilitate proper cleanup and state management. Resources are tracked
    with their workspace context to enable correct workspace-scoped cleanup.

    Attributes:
        workspaces: List of available workspaces
        active_workspace: Currently active workspace
        active_experiment_id: Currently active experiment ID
        experiments_to_delete: Map of experiment_id -> workspace for cleanup
        active_run: Currently active run ID
        runs_to_delete: Map of run_id -> workspace for cleanup
        active_model_name: Currently active registered model name
        models_to_delete: Map of model_name -> workspace for cleanup
        active_user: Currently active user
        user_client: MLflow client authenticated with current user credentials
        users_to_delete: List of users to delete (users are global, not workspace-scoped)
        resource_map: Shared resource map from fixtures
        last_error: Last structured error encountered during test execution
        current_run_id: ID of currently active MLflow run for artifact operations
        temp_artifact_path: Path to temporary artifact file
        temp_artifact_content: Content of temporary artifact
        artifact_list: List of artifacts returned from list_artifacts operation
        downloaded_path: Path where artifact was downloaded
        model: Created or loaded model object
        model_uri: URI of logged model
        artifact_location: Artifact storage URI from run info
    """

    workspaces: list[str] = field(default_factory=list)
    active_workspace: Optional[str] = None
    active_experiment_id: Optional[str] = None
    experiments_to_delete: dict[str, str] = field(default_factory=dict)
    active_run: Optional[str] = None
    runs_to_delete: dict[str, str] = field(default_factory=dict)
    active_model_name: Optional[str] = None
    models_to_delete: dict[str, str] = field(default_factory=dict)
    active_user: Optional[UserInfo] = None
    user_client: Optional[MlflowClient] = None
    users_to_delete: list[UserInfo] = field(default_factory=list)
    resource_map: dict[ResourceType, dict[str, list[str] | str]] = field(default_factory=dict)
    last_error: Optional[ErrorResponse] = None
    current_run_id: Optional[str] = None
    temp_artifact_path: Optional[str] = None
    temp_artifact_content: Optional[str] = None
    artifact_list: Optional[list] = None
    downloaded_path: Optional[str] = None
    model: Optional[Any] = None
    model_uri: Optional[str] = None
    artifact_location: Optional[str] = None

    def add_experiment_for_cleanup(self, experiment_id: str, workspace: str) -> None:
        """Add an experiment to the cleanup list with workspace context.

        Args:
            experiment_id: ID of the experiment to clean up
            workspace: Workspace where the experiment exists

        Raises:
            ValueError: If experiment_id or workspace is empty
        """
        if not experiment_id or not experiment_id.strip():
            logger.error("Attempted to add experiment for cleanup with empty experiment_id")
            raise ValueError("experiment_id cannot be empty")
        if not workspace or not workspace.strip():
            logger.error(f"Attempted to add experiment {experiment_id} for cleanup with empty workspace")
            raise ValueError("workspace cannot be empty")

        self.experiments_to_delete[experiment_id.strip()] = workspace.strip()
        logger.info(f"Added experiment {experiment_id} in workspace '{workspace}' to cleanup list (total: {len(self.experiments_to_delete)})")

    def add_run_for_cleanup(self, run_id: str, workspace: str) -> None:
        """Add a run to the cleanup list with workspace context.

        Args:
            run_id: ID of the run to clean up
            workspace: Workspace where the run exists

        Raises:
            ValueError: If run_id or workspace is empty
        """
        if not run_id or not run_id.strip():
            logger.error("Attempted to add run for cleanup with empty run_id")
            raise ValueError("run_id cannot be empty")
        if not workspace or not workspace.strip():
            logger.error(f"Attempted to add run {run_id} for cleanup with empty workspace")
            raise ValueError("workspace cannot be empty")

        self.runs_to_delete[run_id.strip()] = workspace.strip()
        logger.info(f"Added run {run_id} in workspace '{workspace}' to cleanup list (total: {len(self.runs_to_delete)})")

    def add_model_for_cleanup(self, model_name: str, workspace: str) -> None:
        """Add a registered model to the cleanup list with workspace context.

        Args:
            model_name: Name of the registered model to clean up
            workspace: Workspace where the model exists

        Raises:
            ValueError: If model_name or workspace is empty
        """
        if not model_name or not model_name.strip():
            logger.error("Attempted to add model for cleanup with empty model_name")
            raise ValueError("model_name cannot be empty")
        if not workspace or not workspace.strip():
            logger.error(f"Attempted to add model {model_name} for cleanup with empty workspace")
            raise ValueError("workspace cannot be empty")

        self.models_to_delete[model_name.strip()] = workspace.strip()
        logger.info(f"Added model {model_name} in workspace '{workspace}' to cleanup list (total: {len(self.models_to_delete)})")

    def add_user_for_cleanup(self, user_info: UserInfo) -> None:
        """Add a user to the cleanup list.

        Users are typically global resources, not workspace-scoped.

        Args:
            user_info: User information object

        Raises:
            ValueError: If user_info is None or not a UserInfo instance
        """
        if user_info is None:
            logger.error("Attempted to add None user_info for cleanup")
            raise ValueError("user_info cannot be None")
        if not isinstance(user_info, UserInfo):
            logger.error(f"Attempted to add invalid user_info type: {type(user_info)}")
            raise ValueError("user_info must be a UserInfo instance")

        self.users_to_delete.append(user_info)
        logger.info(f"Added user '{user_info.uname}' to cleanup list (total: {len(self.users_to_delete)})")

