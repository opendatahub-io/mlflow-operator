import logging
import os
import socket
import time
from typing import ClassVar

import mlflow
import pytest
import requests
from mlflow.utils.workspace_utils import WORKSPACE_HEADER_NAME

from .shared import UserInfo, TestData, TestStep
from .constants.config import Config
from .http_utils import get_mlflow_base_uri, get_requests_verify_value
from .actions import (
    action_start_run,
    action_end_run,
    action_create_temp_artifact,
    action_log_artifact,
    action_list_artifacts,
    action_download_artifact,
    action_create_model,
    action_log_model,
    action_load_model,
    action_get_run_info,
    action_create_artifact_connection_secret,
    action_create_mlflowconfig,
    action_wait_for_mlflowconfig_active,
    action_create_experiment,
)
from .validations import (
    validate_artifact_logged,
    validate_artifact_downloaded,
    validate_local_model_created,
    validate_model_logged,
    validate_model_loaded,
    validate_storage,
    validate_run_created,
    validate_run_ended,
    validate_authentication_denied,
    validate_authentication_denied_or_resource_not_found,
    validate_custom_artifact_location,
    validate_no_error,
)
from mlflow_tests.enums import ResourceType, KubeVerb
from .validations.experiment_validations import validate_experiment_created
from .base import TestBase

logger = logging.getLogger(__name__)

RUNS_CUSTOM_ARTIFACT_OVERRIDE_TEST = (
    Config.ARTIFACT_STORAGE == "s3" and not Config.SERVE_ARTIFACTS
)


@pytest.mark.Artifacts
@pytest.mark.smoke
class TestMLflowArtifacts(TestBase):
    """Test Artifact operations with RBAC permissions.

    Tests artifact logging, downloading, model logging/loading, and S3 storage
    verification with different user permission levels (READ, EDIT, MANAGE).
    """

    test_scenarios: ClassVar[list[TestData]] = [
        # Basic artifact workflow tests - CREATE permission
        TestData(
            test_name="User with UPDATE & GET permission can log and download artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.LIST], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact),
                TestStep(action_func=action_list_artifacts, validate_func=validate_artifact_logged),
                TestStep(action_func=action_download_artifact, validate_func=validate_artifact_downloaded),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended)
            ]
        ),
        TestData(
            test_name="User with UPDATE & GET permission can log and load models",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created),
                TestStep(action_func=action_create_model, validate_func=validate_local_model_created),
                TestStep(action_func=action_log_model, validate_func=validate_model_logged),
                TestStep(action_func=action_load_model, validate_func=validate_model_loaded),
                TestStep(action_func=action_end_run, validate_func=validate_run_ended)
            ]
        ),
        TestData(
            test_name="User with UPDATE permission can verify storage for artifacts",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE, KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact),
                TestStep(action_func=action_get_run_info),
                TestStep(action_func=action_end_run, validate_func=validate_storage)
            ]
        ),
        TestData(
            test_name="User with GET permission cannot log models",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS])),
                TestStep(action_func=action_create_model, validate_func=validate_local_model_created),
                TestStep(action_func=action_log_model, validate_func=validate_authentication_denied, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.REGISTERED_MODELS, ResourceType.JOBS]))
            ]
        ),

        # Cross-workspace permission tests
        # Note: These tests verify permission failures when accessing resources in unauthorized workspaces
        TestData(
            test_name="User with GET permission on workspace 1 cannot start run in workspace 2",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = TestStep(
                action_func=action_start_run,
                validate_func=validate_authentication_denied
            )
        ),
        TestData(
            test_name="User with UPDATE permission on workspace 2 cannot log artifacts in workspace 1",
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[1], verbs=[KubeVerb.CREATE, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS])),
                TestStep(action_func=action_create_temp_artifact),
                TestStep(action_func=action_log_artifact, validate_func=validate_authentication_denied, workspace_to_use=Config.WORKSPACES[0], user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE, KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS]))
            ]
        ),

        # Additional negative test cases
        TestData(
            test_name="User with GET permission cannot end run",
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_run_created, user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.UPDATE], resource_types=[ResourceType.EXPERIMENTS])),
                TestStep(action_func=action_end_run, validate_func=validate_authentication_denied,user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]))
            ]
        ),
        TestData(
            test_name="User with UPDATE permission on workspace 1 cannot log model to run in workspace 2",
            workspace_to_use=Config.WORKSPACES[1],
            test_steps = [
                TestStep(
                    action_func=action_start_run,
                    validate_func=validate_run_created,
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[1],
                        verbs=[KubeVerb.GET, KubeVerb.UPDATE],
                        resource_types=[ResourceType.EXPERIMENTS],
                    ),
                ),
                TestStep(action_func=action_create_model, validate_func=validate_local_model_created),
                TestStep(
                    action_func=action_log_model,
                    validate_func=validate_authentication_denied_or_resource_not_found,
                    workspace_to_use=Config.WORKSPACES[0],
                    user_info=UserInfo(
                        workspace=Config.WORKSPACES[0],
                        verbs=[KubeVerb.GET, KubeVerb.UPDATE],
                        resource_types=[ResourceType.EXPERIMENTS],
                    ),
                ),
            ]
        ),
        TestData(
            test_name="User with LIST permission cannot log artifacts without CREATE permission",
            user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.LIST], resource_types=[ResourceType.EXPERIMENTS, ResourceType.JOBS]),
            workspace_to_use=Config.WORKSPACES[0],
            test_steps = [
                TestStep(action_func=action_start_run, validate_func=validate_authentication_denied),
                TestStep(action_func=action_log_artifact, validate_func=validate_authentication_denied)
            ]
        ),

    ] + (
        [
            TestData(
                test_name="Artifacts stored at custom MLflowConfig location",
                user_info=UserInfo(workspace=Config.WORKSPACES[0], verbs=[KubeVerb.CREATE, KubeVerb.UPDATE, KubeVerb.GET], resource_types=[ResourceType.EXPERIMENTS]),
                workspace_to_use=Config.WORKSPACES[0],
                test_steps=[
                    TestStep(action_func=action_create_artifact_connection_secret),
                    TestStep(action_func=action_create_mlflowconfig),
                    TestStep(action_func=action_wait_for_mlflowconfig_active, validate_func=validate_no_error),
                    TestStep(action_func=action_create_experiment, validate_func=validate_experiment_created),
                    TestStep(action_func=action_start_run, validate_func=validate_run_created),
                    TestStep(action_func=action_get_run_info),
                    TestStep(action_func=action_end_run, validate_func=validate_custom_artifact_location),
                ]
            ),
        ]
        if RUNS_CUSTOM_ARTIFACT_OVERRIDE_TEST
        else []
    )

    @pytest.mark.parametrize(
        'test_data', test_scenarios, ids=lambda x: x.test_name)
    def test_mlflow_artifacts(self, create_user_with_permissions, test_data: TestData) -> None:
        """Test artifact operations with user permissions.

        Executes action(s) (if provided) and validates the result based on user permissions.
        Supports both single actions and sequences of actions for complex workflows.

        Args:
            create_user_with_permissions: Fixture to create test users with specific permissions.
            test_data: Test configuration containing user info, actions, and validations.

        Raises:
            AssertionError: If any validation fails.
        """
        logger.info("=" * 80)
        logger.info(f"Starting test: {test_data.test_name}")
        if test_data.user_info:
            verb_names = [verb.value for verb in test_data.user_info.verbs]
            logger.info(f"User verbs: {verb_names}, Resource: {[rt.value for rt in test_data.user_info.resource_types]}")
        if test_data.workspace_to_use:
            logger.info(f"Workspace: {test_data.workspace_to_use}")
        logger.info("=" * 80)

        # Clear any previous error state before starting new test
        self.test_context.last_error = None

        if test_data.user_info:
            # Step 2: Create user with permissions
            logger.info(f"Step 2: Creating user with {verb_names} permissions on {[rt.value for rt in test_data.user_info.resource_types]} in workspace '{test_data.user_info.workspace}'")
            user_info: UserInfo = create_user_with_permissions(
                workspace=test_data.user_info.workspace,
                verbs=test_data.user_info.verbs,
                resource_types=test_data.user_info.resource_types,
                subresources=test_data.user_info.subresources,
                resource_names=test_data.user_info.resource_names,
            )
            logger.info(f"Created user: {user_info.uname}")

            # Step 3: Set test context and workspace
            logger.debug("Step 3: Setting active user and workspace context")
            self.test_context.active_user = user_info
            self.test_context.user_client = user_info.client
            logger.debug(f"Using authenticated MLflow client for user: {user_info.uname}")

        if test_data.workspace_to_use:
            self.test_context.active_workspace = test_data.workspace_to_use
            mlflow.set_workspace(self.test_context.active_workspace)
            logger.info(f"Set active workspace to: {test_data.workspace_to_use}")

        try:
            active_experiment_id = self._set_active_experiment_from_map(
                test_data.workspace_to_use,
            )
            logger.info(f"Set active experiment ID to: {active_experiment_id}")
        except KeyError:
            logger.warning(f"No experiments found in workspace '{test_data.workspace_to_use}', using None")
            self.test_context.active_experiment_id = None


        # Step 4-5: Execute test steps (actions and validations)
        self._execute_test_steps(test_data)

        logger.info(f"Test PASSED: {test_data.test_name}")

    @pytest.mark.skipif(
        Config.ARTIFACT_STORAGE != "s3",
        reason="requires the in-cluster SeaweedFS S3 backend",
    )
    def test_s3_model_download_uses_runner_reachable_presigned_endpoint(
        self, create_user_with_permissions
    ) -> None:
        """Load a model through the multipart download path from the test container.

        Self-hosted SeaweedFS signs URLs using the cluster Service DNS name. The
        integration launcher maps that name to its localhost port-forward, while
        externally managed S3 signs URLs with its externally reachable endpoint.
        """
        if Config.ARTIFACT_BACKEND == "s3":
            signed_url_host = f"minio-service.{Config.MLFLOW_NAMESPACE}.svc.cluster.local"
            assert socket.gethostbyname(signed_url_host) == "127.0.0.1"

        workspace = Config.WORKSPACES[0]
        user = create_user_with_permissions(
            workspace=workspace,
            verbs=[KubeVerb.GET, KubeVerb.UPDATE],
            resource_types=[ResourceType.EXPERIMENTS],
        )
        self.test_context.active_user = user
        self.test_context.user_client = user.client
        self.test_context.active_workspace = workspace
        mlflow.set_workspace(workspace)
        self._set_active_experiment_from_map(workspace)

        action_start_run(self.test_context)
        try:
            action_create_model(self.test_context)
            action_log_model(self.test_context)
            action_load_model(self.test_context)
            assert self.test_context.model.predict([[3]])[0] == pytest.approx(7)
        finally:
            action_end_run(self.test_context)


@pytest.mark.artifacts_server
@pytest.mark.smoke
class TestMLflowArtifactsServer(TestBase):
    def _create_artifact(
        self, create_user_with_permissions
    ) -> tuple[str, str, dict[str, object]]:
        workspace = Config.WORKSPACES[0]
        user = create_user_with_permissions(
            workspace=workspace,
            verbs=[KubeVerb.GET, KubeVerb.UPDATE, KubeVerb.LIST],
            resource_types=[ResourceType.EXPERIMENTS],
        )
        self.test_context.active_user = user
        self.test_context.user_client = user.client
        self.test_context.active_workspace = workspace
        mlflow.set_workspace(workspace)
        self._set_active_experiment_from_map(workspace)

        action_start_run(self.test_context)
        action_create_temp_artifact(self.test_context)
        try:
            action_log_artifact(self.test_context)
        finally:
            action_end_run(self.test_context)

        run_id = self.test_context.current_run_id
        artifact_name = os.path.basename(self.test_context.temp_artifact_path)
        headers = {
            "Authorization": f"Bearer {user.upass}",
            WORKSPACE_HEADER_NAME: workspace,
        }
        request_args = {
            "headers": headers,
            "timeout": Config.REQUEST_TIMEOUT,
            "verify": get_requests_verify_value(),
        }
        return run_id, artifact_name, request_args

    def _exercise_common_artifact_paths(
        self,
        base_uri: str,
        run_id: str,
        artifact_name: str,
        request_args: dict[str, object],
    ) -> None:
        list_response = requests.get(
            f"{base_uri}/ajax-api/2.0/mlflow/artifacts/list",
            params={"run_id": run_id},
            **request_args,
        )
        list_response.raise_for_status()
        assert any(
            file_info.get("path") == artifact_name
            for file_info in list_response.json()["files"]
        )

        download_response = requests.get(
            f"{base_uri}/get-artifact",
            params={"run_uuid": run_id, "path": artifact_name},
            **request_args,
        )
        download_response.raise_for_status()
        assert download_response.text == self.test_context.temp_artifact_content

    def _exercise_multipart_paths(
        self,
        base_uri: str,
        run_id: str,
        request_args: dict[str, object],
    ) -> tuple[str, str]:
        mpu_artifact_path = f"{self.test_context.active_experiment_id}/{run_id}"
        mpu_file = "probe.bin"
        create_mpu_path = (
            f"/api/2.0/mlflow-artifacts/mpu/create/{mpu_artifact_path}"
        )
        abort_mpu_path = (
            f"/api/2.0/mlflow-artifacts/mpu/abort/{mpu_artifact_path}"
        )
        create_response = requests.post(
            f"{base_uri}{create_mpu_path}",
            json={"path": mpu_file, "num_parts": 1},
            **request_args,
        )
        create_response.raise_for_status()
        create_payload = create_response.json()
        upload_id = create_payload["upload_id"]
        assert upload_id
        abort_response = requests.post(
            f"{base_uri}{abort_mpu_path}",
            json={"path": mpu_file, "upload_id": upload_id},
            **request_args,
        )
        abort_response.raise_for_status()
        return create_mpu_path, abort_mpu_path

    def _assert_artifact_deployment_logged_paths(self, expected_paths: set[str]) -> None:
        deadline = time.monotonic() + 30
        observed_paths = set()
        while time.monotonic() < deadline and observed_paths != expected_paths:
            pods = self.k8_manager.core_v1_api.list_namespaced_pod(
                Config.MLFLOW_NAMESPACE,
                label_selector="app=mlflow-artifacts",
            ).items
            for pod in pods:
                logs = self.k8_manager.core_v1_api.read_namespaced_pod_log(
                    pod.metadata.name,
                    Config.MLFLOW_NAMESPACE,
                    container="mlflow-artifacts",
                    since_seconds=60,
                )
                for line in logs.splitlines():
                    observed_paths.update(path for path in expected_paths if path in line)
            if observed_paths != expected_paths:
                time.sleep(1)

        assert observed_paths == expected_paths, (
            "artifact Deployment access logs did not record all Gateway-rewritten requests; "
            f"observed {sorted(observed_paths)}"
        )

    @pytest.mark.skipif(
        not Config.ARTIFACTS_SERVER or not Config.MLFLOW_ARTIFACTS_URI,
        reason="requires an ARTIFACTS_SERVER=true deployment with a direct artifact URI",
    )
    def test_direct_artifact_server_functionality(
        self, create_user_with_permissions
    ) -> None:
        run_id, artifact_name, request_args = self._create_artifact(
            create_user_with_permissions
        )
        self._exercise_common_artifact_paths(
            Config.MLFLOW_ARTIFACTS_URI,
            run_id,
            artifact_name,
            request_args,
        )

    @pytest.mark.skipif(
        not Config.ARTIFACTS_SERVER
        or not Config.MLFLOW_ARTIFACTS_URI
        or Config.ARTIFACT_STORAGE != "s3",
        reason="requires an S3-backed artifact server with a direct artifact URI",
    )
    def test_direct_artifact_server_multipart_functionality(
        self, create_user_with_permissions
    ) -> None:
        run_id, _, request_args = self._create_artifact(create_user_with_permissions)
        self._exercise_multipart_paths(
            Config.MLFLOW_ARTIFACTS_URI,
            run_id,
            request_args,
        )

    @pytest.mark.skipif(
        not Config.ARTIFACTS_SERVER or not Config.ARTIFACTS_SERVER_GATEWAY,
        reason="requires live artifact-server Gateway rewrite validation",
    )
    def test_gateway_compatibility_paths_are_served_by_artifact_deployment(
        self, create_user_with_permissions
    ) -> None:
        run_id, artifact_name, request_args = self._create_artifact(
            create_user_with_permissions
        )
        self._exercise_common_artifact_paths(
            get_mlflow_base_uri(),
            run_id,
            artifact_name,
            request_args,
        )
        self._assert_artifact_deployment_logged_paths(
            {
                "/mlflow-artifacts/ajax-api/2.0/mlflow/artifacts/list",
                "/mlflow-artifacts/get-artifact",
            }
        )

    @pytest.mark.skipif(
        not Config.ARTIFACTS_SERVER
        or not Config.ARTIFACTS_SERVER_GATEWAY
        or Config.ARTIFACT_STORAGE != "s3",
        reason="requires live S3 multipart Gateway rewrite validation",
    )
    def test_gateway_multipart_paths_are_served_by_artifact_deployment(
        self, create_user_with_permissions
    ) -> None:
        run_id, _, request_args = self._create_artifact(create_user_with_permissions)
        create_mpu_path, abort_mpu_path = self._exercise_multipart_paths(
            get_mlflow_base_uri(),
            run_id,
            request_args,
        )
        self._assert_artifact_deployment_logged_paths(
            {
                f"/mlflow-artifacts{create_mpu_path}",
                f"/mlflow-artifacts{abort_mpu_path}",
            }
        )
