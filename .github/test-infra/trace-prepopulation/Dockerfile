# Test-only predecessor runtime. MLflow 3.16.1 has Alembic head b7e2c1a4d9f3.
# Plugin 1.7.1 includes the authorization rules for MLflow 3.16.
# SQLAlchemy 2.1 selects psycopg by default for postgresql://, while MLflow's db extra installs psycopg2.
FROM python:3.12-slim-bookworm@sha256:da54eb86849fe19be7584d8b3f291fcf0c53c196760ab3d70dc6382f681ef9c0

ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

RUN pip install --no-cache-dir \
      'mlflow[db,genai]==3.16.1' \
      'mlflow-kubernetes-plugins==1.7.1' \
      'SQLAlchemy>=2.0,<2.1' && \
    useradd --uid 1001 --no-create-home mlflow

USER 1001
WORKDIR /tmp
