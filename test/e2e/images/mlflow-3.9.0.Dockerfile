FROM registry.access.redhat.com/ubi9/python-312:latest

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 0
RUN pip install --no-cache-dir "mlflow==3.9.0" "psycopg2-binary"
USER 1001
