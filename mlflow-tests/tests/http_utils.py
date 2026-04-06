"""Shared HTTP helpers for integration-style test requests."""

from tests.constants.config import Config


def get_mlflow_base_uri() -> str:
    """Return the configured MLflow base URI without a trailing slash."""
    return Config.MLFLOW_URI.rstrip("/")


def get_requests_verify_value() -> bool | str:
    """Return the requests-compatible TLS verify value for the current test config."""
    if str(Config.DISABLE_TLS).lower() in {"1", "true", "yes", "y"}:
        return False
    if Config.CA_BUNDLE:
        return Config.CA_BUNDLE
    return True
