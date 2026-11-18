# CI-only predecessor MLflow image for the trace analytics prepopulation
# workflow (RHOAIENG-99736).
#
# No suitable published ODH/RHOAI predecessor image has been identified.
# This fixture uses MLflow 3.16.0, whose schema head is b7e2c1a4d9f3, to
# serve traffic while the current Job image prepopulates that revision.
# It is not a supported RHOAI runtime or proof of a released upgrade path.
# The dependencies are:
#
#   - mlflow==3.16.0            server whose Alembic head is b7e2c1a4d9f3
#   - mlflow-kubernetes-plugins==1.7.1
#                               plugin line matched to MLflow <3.17 (the v1
#                               line; v2.x requires MLflow 3.17)
#   - psycopg2-binary           PostgreSQL backend driver
#   - SQLAlchemy>=2.0,<2.1     SQLAlchemy 2.1 selects psycopg by default for
#                               postgresql:// while MLflow 3.16 declares
#                               sqlalchemy<3, so pin below 2.1 to keep the
#                               psycopg2 driver the command expects
#
# The live predecessor server only needs to serve traffic at the old schema;
# the prepopulation Job itself runs the current MLflow runtime image, which
# contains the `mlflow db prepopulate-trace-analytics` command.
#
# The Kubernetes plugin requires Python >=3.12, so upstream MLflow 3.16.0's
# Python 3.11 base cannot be reused. Pin the base by digest to avoid tag drift.

FROM python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

RUN pip install --no-cache-dir \
    "mlflow==3.16.0" \
    "mlflow-kubernetes-plugins==1.7.1" \
    "psycopg2-binary" \
    "SQLAlchemy>=2.0,<2.1" && \
    useradd --uid 1001 --no-create-home mlflow

# Operator-rendered pods run with runAsNonRoot: true. The explicit user entry
# keeps $HOME (/tmp) writable for the numeric UID.
USER 1001
