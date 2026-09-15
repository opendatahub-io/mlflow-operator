import os


class Config:

    LOCAL: bool = os.getenv("LOCAL", "false") == "true"
    ADMIN_USERNAME: str = os.getenv("admin_uname", "")
    ADMIN_PASSWORD: str = os.getenv("admin_pass", "")
    K8_API_TOKEN: str = os.getenv("kube_token", "")
    MLFLOW_URI: str = os.getenv("MLFLOW_TRACKING_URI", "https://localhost:8080/mlflow")
    DISABLE_TLS: str = os.getenv("DISABLE_TLS", "true")
    CA_BUNDLE: str = os.getenv("ca_bundle", "")
    REQUEST_TIMEOUT: int = int(os.getenv("MLFLOW_REQUEST_TIMEOUT", "30"))
    ARTIFACT_STORAGE = os.getenv("artifact_storage", "file")
    ARTIFACT_BACKEND = os.getenv("artifact_backend", ARTIFACT_STORAGE)
    SERVE_ARTIFACTS = os.getenv("serve_artifacts", "true") == "true"
    ARTIFACTS_SERVER = os.getenv("artifacts_server", "false") == "true"
    ARTIFACTS_SERVER_GATEWAY = os.getenv("artifacts_server_gateway", "false") == "true"
    MLFLOW_ARTIFACTS_URI = os.getenv("MLFLOW_ARTIFACTS_URI", "").rstrip("/")
    TRACE_ARCHIVAL_ENABLED = os.getenv("trace_archival_enabled", "false") == "true"
    GARBAGE_COLLECTION_ENABLED = os.getenv("garbage_collection_enabled", "false") == "true"
    MLFLOW_NAMESPACE = os.getenv("mlflow_namespace", "opendatahub")
    AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    S3_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", "")
    S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
    TRACE_ARCHIVAL_RETENTION = os.getenv("TRACE_ARCHIVAL_RETENTION", "30d")
    WORKSPACE_LABEL_SELECTOR: str = os.getenv("WORKSPACE_LABEL_SELECTOR", "")

    WORKSPACES: list[str] = [
        workspace.strip()
        for workspace in os.getenv("workspaces", "workspace1,workspace2").split(",")
        if workspace.strip()  # Filter out empty strings after stripping
    ]

    UPGRADE_SUPPORTED_VERSION: str = os.getenv("MLFLOW_TEST_SUPPORTED_VERSION", "").strip()
    UPGRADE_VERSION_CONFIGMAP_NAME: str = "mlflow-upgrade-test-version"
    UPGRADE_VERSION_CONFIGMAP_KEY: str = "pre_upgrade_version"
    UPGRADE_TEST_WORKSPACE: str = os.getenv(
        "upgrade_test_workspace",
        os.getenv("upgrade_workspace", "mlflow-upgrade-test-workspace"),
    ).strip()
