import os


class Config:

    LOCAL: bool = os.getenv("local", "false") == "true"
    ADMIN_USERNAME: str = os.getenv("admin_uname", "")
    ADMIN_PASSWORD: str = os.getenv("admin_pass", "")
    K8_API_TOKEN: str = os.getenv("kube_token", "")
    MLFLOW_URI: str = os.getenv("MLFLOW_TRACKING_URI", "https://localhost:8080")
    CA_BUNDLE: str = os.getenv("ca_bundle", "")
    ARTIFACT_STORAGE = os.getenv("artifact_storage", "file")

    WORKSPACES: list[str] = [
        workspace.strip()
        for workspace in os.getenv("workspaces", "workspace1,workspace2").split(",")
        if workspace.strip()  # Filter out empty strings after stripping
    ]
