"""Shared HTTP helpers for integration-style test requests."""

import inspect
import os
from urllib.parse import urlsplit

from tests.constants.config import Config


def get_mlflow_base_uri() -> str:
    """Return the configured MLflow base URI without a trailing slash."""
    return Config.MLFLOW_URI.rstrip("/")


def mlflow_client_honors_s3_ignore_tls() -> bool:
    """Return whether presigned chunk downloads honor ``MLFLOW_S3_IGNORE_TLS``.

    Presigned artifact-URL chunk downloads only pass a TLS ``verify`` value to
    ``requests`` starting with mlflow 3.17 (and the matching opendatahub-io
    fork builds). Older clients — for example the PyPI 3.15.x releases locked
    by older test-image builds — silently ignore ``MLFLOW_S3_IGNORE_TLS`` during
    these downloads. An untrusted certificate can then cause several minutes
    of retries and a misleading "please ensure that the path is correct" error.

    Detect the capability from the ``download_chunk`` signature instead of a
    version string so fork builds without upstream version metadata are
    handled correctly.
    """
    try:
        from mlflow.utils.request_utils import download_chunk
    except ImportError:  # pragma: no cover - very old/unusual client builds
        return False
    return "verify" in inspect.signature(download_chunk).parameters


def check_mlflow_client_tls_compatibility(ignore_s3_tls: bool) -> None:
    """Check TLS support for MLflow-served artifacts on an explicit HTTPS S3 endpoint.

    Args:
        ignore_s3_tls: whether S3/presigned-URL TLS verification is being
            disabled for this run (the value written to
            ``MLFLOW_S3_IGNORE_TLS``).

    Raises:
        RuntimeError: when the configuration permits presigned HTTPS downloads
            with TLS verification disabled but the client lacks that capability.
    """
    if (
        not ignore_s3_tls
        or Config.ARTIFACT_STORAGE != "s3"
        or not (Config.SERVE_ARTIFACTS or Config.ARTIFACTS_SERVER)
        or urlsplit(Config.S3_URL).scheme != "https"
    ):
        return
    if (
        Config.CA_BUNDLE
        or os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("CURL_CA_BUNDLE")
    ):
        # The localhost port-forward needs verification disabled, but presigned
        # Service URLs can still verify against a configured CA bundle.
        return
    if os.environ.get("MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD", "").lower() in {"false", "0"}:
        return
    if not mlflow_client_honors_s3_ignore_tls():
        import mlflow

        raise RuntimeError(
            "MLFLOW_S3_IGNORE_TLS=true is set for MLflow-served artifacts on an HTTPS "
            "S3 endpoint without a configured CA bundle, but the installed mlflow "
            f"client ({mlflow.__version__}) does not support disabling TLS verification "
            "for presigned artifact-URL downloads (support was added in mlflow 3.17). "
            "Upgrade the mlflow dependency of the mlflow-tests image to mlflow >= 3.17 (or an "
            "opendatahub-io/mlflow build that includes the presigned-URL verify "
            "support), or configure a trusted CA bundle before running this suite."
        )


def get_requests_verify_value() -> bool | str:
    """Return the requests-compatible TLS verify value for the current test config."""
    if str(Config.DISABLE_TLS).lower() in {"1", "true", "yes", "y"}:
        return False
    if Config.CA_BUNDLE:
        return Config.CA_BUNDLE
    return True


def get_s3_verify_value(endpoint_url: str | None = None) -> bool | str:
    """Return the verification setting for direct S3 and presigned URL requests."""
    if endpoint_url and endpoint_url.startswith("https://localhost:"):
        # SeaweedFS is port-forwarded through localhost in the self-hosted TLS suite,
        # but its certificate SANs only include the in-cluster service DNS names.
        return False
    if Config.CA_BUNDLE:
        return Config.CA_BUNDLE
    return str(Config.DISABLE_TLS).lower() not in {"1", "true", "yes", "y"}


def configure_ca_bundle_environment() -> None:
    bundle_variables = (
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "AWS_CA_BUNDLE",
    )
    if Config.CA_BUNDLE:
        for name in bundle_variables:
            os.environ[name] = Config.CA_BUNDLE
    else:
        for name in bundle_variables:
            if os.environ.get(name) == "":
                os.environ.pop(name)
