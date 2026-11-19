#!/bin/bash
# collect-debug-logs.sh: Collect namespace-scoped MLflow debug artifacts.
#
# Usage:
#   collect-debug-logs.sh --namespace <ns> [--output-dir <dir>]
#
# Called by test-run.sh on pre-pytest readiness failures, test failure, and by
# the GHA collect-debug-logs action.
# Writes to the output directory so logs are available in CI artifacts regardless
# of whether the runner is GHA, Jenkins, or local.

set -euo pipefail

NAMESPACE=""
OUTPUT_DIR="debug-logs"

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --namespace)
            [[ "$#" -lt 2 ]] && { echo "ERROR: --namespace requires a value." >&2; exit 1; }
            NAMESPACE="$2"; shift ;;
        --output-dir)
            [[ "$#" -lt 2 ]] && { echo "ERROR: --output-dir requires a value." >&2; exit 1; }
            OUTPUT_DIR="$2"; shift ;;
        *) echo "Unknown parameter: $1" >&2; exit 1 ;;
    esac
    shift
done

if [[ -z "$NAMESPACE" ]]; then
    echo "ERROR: --namespace is required." >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
echo "Collecting debug logs for namespace '${NAMESPACE}' into ${OUTPUT_DIR}..."

kubectl get namespaces > "${OUTPUT_DIR}/namespaces.txt" 2>&1 || true

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "Namespace '${NAMESPACE}' not found; skipping." | tee "${OUTPUT_DIR}/skip.txt"
    exit 0
fi

kubectl get mlflow -n "$NAMESPACE" -o yaml > "${OUTPUT_DIR}/mlflow-cr.yaml" 2>&1 || true
kubectl get mlflow -n "$NAMESPACE" -o jsonpath='{range .items[*]}{.metadata.name}{" url="}{.status.url}{" version="}{.status.version}{"\n"}{range .status.conditions[*]}{.type}={.status} reason={.reason} message={.message}{"\n"}{end}{end}' \
    > "${OUTPUT_DIR}/mlflow-cr.status.txt" 2>&1 || true
kubectl get pods -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/pods.txt" 2>&1 || true
kubectl get deployments -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/deployments.txt" 2>&1 || true
kubectl get jobs -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/jobs.txt" 2>&1 || true
kubectl get svc -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/services.txt" 2>&1 || true
kubectl get route -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/routes.txt" 2>&1 || true
kubectl get ingress -n "$NAMESPACE" -o wide > "${OUTPUT_DIR}/ingress.txt" 2>&1 || true
kubectl get events -n "$NAMESPACE" --sort-by='.lastTimestamp' > "${OUTPUT_DIR}/events.txt" 2>&1 || true

for deployment in mlflow controller-manager; do
    kubectl describe deployment "$deployment" -n "$NAMESPACE" \
        > "${OUTPUT_DIR}/${deployment}-deployment.describe.txt" 2>&1 || true
done

# Raw container logs are collected per label selector. The prepopulation Job
# pod is deliberately excluded from raw log collection (its component label
# also matches app=mlflow): the prepopulation suite's hygiene contract keeps
# raw Job logs out of uploaded debug artifacts, and its lifecycle diagnostics
# are already covered by the pods/jobs/events listings above. The `!=`
# requirement also matches pods that do not carry the component label at all,
# so the MLflow server pod is unaffected.
while IFS='|' read -r label suffix; do
    label="${label#"${label%%[![:space:]]*}"}"
    mapfile -t pods < <(
        kubectl get pods -n "$NAMESPACE" -l "$label" \
            -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
    )
    [ "${#pods[@]}" -eq 0 ] && continue

    for pod in "${pods[@]}"; do
        echo "  Collecting logs for pod: ${pod}"
        kubectl describe pod "$pod" -n "$NAMESPACE" \
            > "${OUTPUT_DIR}/${pod}${suffix}.describe.txt" 2>&1 || true
        kubectl logs "$pod" -n "$NAMESPACE" --all-containers=true \
            > "${OUTPUT_DIR}/${pod}${suffix}.log" 2>&1 || true
        kubectl logs "$pod" -n "$NAMESPACE" --all-containers=true --previous \
            > "${OUTPUT_DIR}/${pod}${suffix}.previous.log" 2>&1 || true
    done
done <<'EOF'
    app=mlflow,component!=mlflow-trace-analytics-prepopulation|
    control-plane=controller-manager|-operator
    mlflow.opendatahub.io/migration-job=true|-migration
    app=seaweedfs|-seaweedfs
    app=mlflow-postgres|-postgres
EOF

echo "Debug log collection complete."
