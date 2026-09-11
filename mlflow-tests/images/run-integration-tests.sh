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
#   ARTIFACTS_SERVER            true | false (default: false)
#   ARTIFACTS_SERVER_GATEWAY    true | false (default: false)
#   PYTEST_ARGS                 extra pytest flags
#   PYTEST_MARK_EXPRESSION      optional pytest -m expression
#   TEST_RESULTS_DIR            host path for JUnit XML output (default: test-results)
#   SKIP_DEPLOYMENT             forward deployment reuse behavior to test-run.sh
#   SKIP_OPERATOR               forward operator reuse behavior to test-run.sh
#   SKIP_CLEANUP                forward cleanup behavior to test-run.sh
#   CLEANUP_REUSED_RESOURCES    forward reused-resource cleanup behavior to test-run.sh
#   MLFLOW_TEST_SUPPORTED_VERSION
#                               override the version used to select upgrade datasets
#   upgrade_test_workspace      namespace used by upgrade pre/post phases

set -euo pipefail

results_dir="${TEST_RESULTS_DIR:-test-results}"
mkdir -p "$results_dir"

if [ "${ARTIFACTS_SERVER:-false}" = "true" ]; then
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  if ! kubectl get crd httproutes.gateway.networking.k8s.io >/dev/null 2>&1; then
    kubectl apply -f "$repo_root/test/crd/httproutes.gateway.networking.k8s.io.yaml"
  fi
  # A newly-created CRD can briefly have no status.conditions. kubectl wait
  # treats that transient state as an accessor error instead of waiting, so
  # poll the condition directly while the API server finishes establishing it.
  crd_established=false
  for attempt in {1..60}; do
    crd_status="$(kubectl get crd httproutes.gateway.networking.k8s.io \
      -o 'jsonpath={.status.conditions[?(@.type=="Established")].status}' 2>/dev/null || true)"
    if [ "$crd_status" = "True" ]; then
      crd_established=true
      break
    fi
    sleep 1
  done
  if [ "$crd_established" != "true" ]; then
    echo "ERROR: HTTPRoute CRD did not become Established within 60 seconds" >&2
    exit 1
  fi
fi

pytest_marker_args=()
if [ -n "${PYTEST_MARK_EXPRESSION:-}" ]; then
  pytest_marker_args=(-m "$PYTEST_MARK_EXPRESSION")
fi

docker_args=(--rm --network host)
# MLflow signs SeaweedFS URLs with its in-cluster service endpoint. test-run.sh
# port-forwards that service to the runner, so make the exact signed-URL host
# resolve to the runner loopback from this host-networked test container.
if [[ ",${ARTIFACT_BACKENDS}," == *,s3,* ]]; then
  docker_args+=(
    --add-host "minio-service.${NAMESPACE}.svc.cluster.local:127.0.0.1"
  )
fi

for name in \
  SKIP_DEPLOYMENT \
  SKIP_OPERATOR \
  SKIP_CLEANUP \
  CLEANUP_REUSED_RESOURCES \
  MLFLOW_TEST_SUPPORTED_VERSION \
  upgrade_test_workspace; do
  if [[ -v "$name" ]]; then
    docker_args+=(-e "$name=${!name}")
  fi
done

set +e
docker run "${docker_args[@]}" \
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
  -e ARTIFACTS_SERVER="${ARTIFACTS_SERVER:-false}" \
  -e ARTIFACTS_SERVER_GATEWAY="${ARTIFACTS_SERVER_GATEWAY:-false}" \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_S3_BUCKET="$AWS_S3_BUCKET" \
  -e BUCKET="$AWS_S3_BUCKET" \
  -e WORKSPACE_LABEL_SELECTOR="${WORKSPACE_LABEL_SELECTOR:-}" \
  -e POSTGRES_TLS="${POSTGRES_TLS:-false}" \
  -e SEAWEEDFS_TLS="${SEAWEEDFS_TLS:-false}" \
  -e TEST_RESULTS_DIR="/mlflow/results" \
  "$MLFLOW_TESTS_RUNTIME_IMAGE" \
  ${PYTEST_ARGS:-} "${pytest_marker_args[@]}"

exit_code=$?
set -e
exit "$exit_code"
