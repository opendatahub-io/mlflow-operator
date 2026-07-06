#!/usr/bin/env bash
# run-integration-tests.sh — launch the dockerized integration test suite.
#
# This script is the single source of truth for how integration tests are
# executed in CI. Both the operator and mlflow workflows call it so that
# changes to the docker run invocation are picked up everywhere.
#
# Required env vars:
#   MLFLOW_TESTS_RUNTIME_IMAGE  test harness image
#   NAMESPACE                   target k8s namespace
#   OPERATOR_RUNTIME_IMAGE      operator image loaded into Kind
#   MLFLOW_RUNTIME_IMAGE        mlflow server image loaded into Kind
#   BACKEND_STORE               sqlite | postgres
#   REGISTRY_STORE              sqlite | postgres
#   ARTIFACT_BACKENDS           file | s3 | file,s3
#   SERVE_ARTIFACTS             true | false
#   AWS_ACCESS_KEY_ID           S3 credentials
#   AWS_SECRET_ACCESS_KEY       S3 credentials
#   AWS_S3_BUCKET               S3 bucket name
#
# Optional:
#   WORKSPACE_LABEL_SELECTOR    label selector JSON (default: empty)
#   POSTGRES_TLS                true | false (default: false)
#   SEAWEEDFS_TLS               true | false (default: false)
#   PYTEST_ARGS                 extra pytest flags
#   TEST_RESULTS_DIR            host path for JUnit XML output (default: test-results)

set -euo pipefail

results_dir="${TEST_RESULTS_DIR:-test-results}"
mkdir -p "$results_dir"

set +e
docker run --rm --network host \
  -v "$HOME/.kube:/mlflow/.kube:ro,z" \
  -v "$(cd "$results_dir" && pwd):/mlflow/results:z" \
  -e DEPLOY_MLFLOW_OPERATOR=false \
  -e NAMESPACE="$NAMESPACE" \
  -e MLFLOW_OPERATOR_IMAGE="$OPERATOR_RUNTIME_IMAGE" \
  -e MLFLOW_IMAGE="$MLFLOW_RUNTIME_IMAGE" \
  -e BACKEND_STORE="$BACKEND_STORE" \
  -e REGISTRY_STORE="$REGISTRY_STORE" \
  -e ARTIFACT_BACKENDS="$ARTIFACT_BACKENDS" \
  -e SERVE_ARTIFACTS="$SERVE_ARTIFACTS" \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_S3_BUCKET="$AWS_S3_BUCKET" \
  -e BUCKET="$AWS_S3_BUCKET" \
  -e WORKSPACE_LABEL_SELECTOR="${WORKSPACE_LABEL_SELECTOR:-}" \
  -e POSTGRES_TLS="${POSTGRES_TLS:-false}" \
  -e SEAWEEDFS_TLS="${SEAWEEDFS_TLS:-false}" \
  -e TEST_RESULTS_DIR="/mlflow/results" \
  "$MLFLOW_TESTS_RUNTIME_IMAGE" \
  ${PYTEST_ARGS:-}

exit_code=$?
set -e
exit "$exit_code"
