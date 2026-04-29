ARG BASE_IMAGE=quay.io/opendatahub/mlflow:odh-stable
FROM ${BASE_IMAGE}

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 0
RUN microdnf install -y python3.12 python3.12-pip && \
    microdnf clean all && \
    /usr/bin/python3.12 -m pip install --no-cache-dir "mlflow==3.10.0"
USER 1001
