import os


class Config:

    LOCAL: bool = os.getenv("LOCAL", "false") == "true"
    ADMIN_USERNAME: str = os.getenv("admin_uname", "")
    ADMIN_PASSWORD: str = os.getenv("admin_pass", "")
    K8_API_TOKEN: str = os.getenv("kube_token", "")
    MLFLOW_URI: str = os.getenv("MLFLOW_TRACKING_URI", "https://localhost:8080")
    DISABLE_TLS: str = os.getenv("DISABLE_TLS", "true")
    CA_BUNDLE: str = os.getenv("ca_bundle", "")
    REQUEST_TIMEOUT: int = int(os.getenv("MLFLOW_REQUEST_TIMEOUT", "30"))
    ARTIFACT_STORAGE = os.getenv("artifact_storage", "file")
    SERVE_ARTIFACTS = os.getenv("serve_artifacts", "true") == "true"
    AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    S3_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", "")
    S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
    BACKEND_STORE_URI: str = os.getenv("MLFLOW_BACKEND_STORE_URI", "postgresql://postgres:mysecretpassword@localhost:5432/mydatabase")

    WORKSPACE_LABEL_SELECTOR: str = os.getenv("WORKSPACE_LABEL_SELECTOR", "")

    WORKSPACES: list[str] = [
        workspace.strip()
        for workspace in os.getenv("workspaces", "workspace1,workspace2").split(",")
        if workspace.strip()  # Filter out empty strings after stripping
    ]
