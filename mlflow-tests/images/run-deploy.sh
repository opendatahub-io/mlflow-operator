#!/bin/bash
# run-deploy.sh: Deploy MLflow and run integration tests.
#
# Delegates cluster-level deployment to deploy.py (MLflow namespace, operator via kustomize,
# infrastructure, credentials secrets, and the MLflow CR itself). On OpenShift/OLM clusters
# the operator is patched via CSV instead; set DEPLOY_MLFLOW_OPERATOR=true to activate that path.
#
# Replaces test-run.sh. Unlike test-run.sh this script never generates MLflow CR YAML directly —
# all CR construction is owned by deploy.py so changes only need to happen in one place.
#
# Platform support:
#   Kind/vanilla Kubernetes: DEPLOY_MLFLOW_OPERATOR=false (default) — deploy.py manages everything
#   OpenShift/OLM:           DEPLOY_MLFLOW_OPERATOR=true             — CSV patching via patch-csv.sh,
#                                                                       then deploy.py --skip-operator
#                                                                       Set SKIP_INFRASTRUCTURE=true
#                                                                       separately if infra is pre-existing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_PY="${DEPLOY_PY:-$REPO_ROOT/.github/actions/deploy/deploy.py}"

# Source env defaults and CSV-patching helpers
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -o allexport
    source "$SCRIPT_DIR/.env"
    set +o allexport
fi
# shellcheck source=patch-csv.sh
source "$SCRIPT_DIR/patch-csv.sh"

# ─── Defaults ─────────────────────────────────────────────────────────────────

NAMESPACE="${NAMESPACE:-opendatahub}"
MLFLOW_NAME="mlflow"
# SA name is set by the operator's Helm chart; see internal/controller/constants.go
MLFLOW_SA_NAME="${MLFLOW_SA_NAME:-mlflow-sa}"

SKIP_DEPLOYMENT="${SKIP_DEPLOYMENT:-false}"
SKIP_CLEANUP="${SKIP_CLEANUP:-false}"
IN_CLUSTER_MODE="${IN_CLUSTER_MODE:-true}"

# Set DEPLOY_MLFLOW_OPERATOR=true on OpenShift clusters where the operator runs under OLM.
# When true the script patches the OLM CSV instead of deploying the operator via kustomize
# and passes --skip-operator to deploy.py. Infrastructure (postgres/seaweedfs) is NOT
# automatically skipped — set SKIP_INFRASTRUCTURE=true separately if infra is pre-existing.
DEPLOY_MLFLOW_OPERATOR="${DEPLOY_MLFLOW_OPERATOR:-false}"

# Repository coordinates used when DEPLOY_MLFLOW_OPERATOR=true to download manifests for
# CSV patching (see patch-csv.sh).
MLFLOW_OPERATOR_OWNER="${MLFLOW_OPERATOR_OWNER:-opendatahub-io}"
MLFLOW_OPERATOR_REPO="${MLFLOW_OPERATOR_REPO:-mlflow-operator}"
MLFLOW_OPERATOR_BRANCH="${MLFLOW_OPERATOR_BRANCH:-main}"

# When DEPLOY_MLFLOW_OPERATOR=false (Kind) you can still skip just the operator deployment
# if it is already installed (e.g. repeated test runs against a long-lived cluster).
SKIP_OPERATOR="${SKIP_OPERATOR:-false}"

# Set SKIP_INFRASTRUCTURE=true to skip deploying PostgreSQL/SeaweedFS via deploy.py.
# Use this when infra is pre-provisioned (e.g. an existing OpenShift cluster).
# Credentials secrets are always created regardless of this flag.
SKIP_INFRASTRUCTURE="${SKIP_INFRASTRUCTURE:-false}"

MLFLOW_TAG="master"
MLFLOW_IMAGE_REPO="quay.io/opendatahub/mlflow"
MLFLOW_IMAGE=""
MLFLOW_OPERATOR_IMAGE="quay.io/opendatahub/mlflow-operator:odh-stable"

STORAGE_TYPE="${STORAGE_TYPE:-file}"
DB_TYPE="${DB_TYPE:-sqlite}"

RANDOM_SUFFIX=$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)
WORKSPACE_LIST="${workspaces:-workspace1-${RANDOM_SUFFIX},workspace2-${RANDOM_SUFFIX}}"
# Export so pytest (Config.WORKSPACES) reads the same names RBAC is set up for
export workspaces="$WORKSPACE_LIST"

_CREATED_WORKSPACES=""
PF_PID=""

# ─── Usage ────────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --mlflow-image=IMAGE               Full MLflow image reference (overrides --mlflow-tag)
  --mlflow-tag=TAG                   Image tag for $MLFLOW_IMAGE_REPO (default: master)
  --mlflow-operator-image=IMAGE      Full MLflow operator image reference
  --storage-type=TYPE                Artifact storage backend: file|s3 (default: file)
  --db-type=TYPE                     Metadata store backend: sqlite|postgres (default: sqlite)
  --deploy-mlflow-operator           Patch the OLM CSV instead of deploying the operator via
                                     kustomize (use on OpenShift/OLM clusters)
  --mlflow-operator-owner=OWNER      GitHub owner for CSV manifest download (default: opendatahub-io)
  --mlflow-operator-repo=REPO        GitHub repo for CSV manifest download (default: mlflow-operator)
  --mlflow-operator-branch=BRANCH    GitHub branch for CSV manifest download (default: main)
  --skip-deployment                  Skip all cluster deployment (assume MLflow is already running)
  --skip-operator                    Skip operator deployment only (kustomize path; assumes operator
                                     is already installed; ignored when --deploy-mlflow-operator is set)
  --skip-infrastructure              Skip deploying PostgreSQL/SeaweedFS (credentials secrets still
                                     created); set automatically when --deploy-mlflow-operator is used
  --skip-cleanup                     Leave all created resources in place after the run

Environment variables (can also be set in images/.env):
  NAMESPACE                  Target namespace (default: opendatahub)
  MLFLOW_SA_NAME             Service account name created by the operator (default: mlflow-sa)
  IN_CLUSTER_MODE            true|false — false enables port-forwarding for local access (default: true)
  TEST_RESULTS_DIR           Directory for JUnit XML output (default: /tmp/test-results)
  workspaces                 Comma-separated workspace namespace list
  DEPLOY_MLFLOW_OPERATOR     true|false — enable CSV patching (OpenShift/OLM path)
  MLFLOW_OPERATOR_OWNER/REPO/BRANCH  GitHub coordinates for manifest download
  SKIP_INFRASTRUCTURE        true|false — skip infrastructure deployment
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BUCKET, S3_ENDPOINT_URL  (S3 storage)
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME                    (PostgreSQL)
EOF
}

# ─── Argument parsing ─────────────────────────────────────────────────────────

while [ "$#" -gt 0 ]; do
    case "$1" in
        --mlflow-image=*)              MLFLOW_IMAGE="${1#*=}";               shift ;;
        --mlflow-tag=*)                MLFLOW_TAG="${1#*=}";                  shift ;;
        --mlflow-operator-image=*)     MLFLOW_OPERATOR_IMAGE="${1#*=}";      shift ;;
        --storage-type=*)              STORAGE_TYPE="${1#*=}";                shift ;;
        --db-type=*)                   DB_TYPE="${1#*=}";                      shift ;;
        --deploy-mlflow-operator)      DEPLOY_MLFLOW_OPERATOR="true";         shift ;;
        --mlflow-operator-owner=*)     MLFLOW_OPERATOR_OWNER="${1#*=}";      shift ;;
        --mlflow-operator-repo=*)      MLFLOW_OPERATOR_REPO="${1#*=}";       shift ;;
        --mlflow-operator-branch=*)    MLFLOW_OPERATOR_BRANCH="${1#*=}";     shift ;;
        --skip-deployment)             SKIP_DEPLOYMENT="true";                shift ;;
        --skip-operator)               SKIP_OPERATOR="true";                  shift ;;
        --skip-infrastructure)         SKIP_INFRASTRUCTURE="true";            shift ;;
        --skip-cleanup)                SKIP_CLEANUP="true";                   shift ;;
        --help|-h)                     usage; exit 0 ;;
        *) echo "Unknown argument: $1"; usage; exit 1 ;;
    esac
done

MLFLOW_RESOLVED_IMAGE="${MLFLOW_IMAGE:-${MLFLOW_IMAGE_REPO}:${MLFLOW_TAG}}"

API_BASE="https://${MLFLOW_NAME}.${NAMESPACE}.svc.cluster.local:8443"

# ─── Cleanup ──────────────────────────────────────────────────────────────────

cleanup() {
    echo "🧹 Cleaning up resources..."

    if [ -n "$PF_PID" ] && kill -0 "$PF_PID" 2>/dev/null; then
        echo "  Stopping port-forward (pid $PF_PID)"
        kill "$PF_PID"
    fi

    # Remove workspace role bindings first, then namespaces created by this script
    for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs)
        [ -z "$ws" ] && continue
        kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
            -n "$ws" --ignore-not-found 2>/dev/null || true
    done
    for ws in $(echo "$_CREATED_WORKSPACES" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs)
        [ -z "$ws" ] && continue
        kubectl delete namespace "$ws" --ignore-not-found 2>/dev/null || true
    done

    kubectl delete mlflow "$MLFLOW_NAME" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
    kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
        -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrolebinding "mlflow-auth-delegator-${MLFLOW_NAME}" \
        --ignore-not-found 2>/dev/null || true
}

if [ "$SKIP_CLEANUP" != "true" ]; then
    trap cleanup EXIT
fi

# ─── CSV patching (OpenShift/OLM) ─────────────────────────────────────────────
# Patches the parent *platform* operator's CSV to mount a volume with the MLflow
# operator manifests from the given branch, then restarts it so it picks them up.
# This path applies only when the MLflow operator is embedded inside a platform
# operator (ODH/RHOAI). When the MLflow operator runs standalone
# (mlflow-operator-controller-manager), the CSV patch is skipped automatically.

if [ "$DEPLOY_MLFLOW_OPERATOR" = "true" ] && [ "$SKIP_DEPLOYMENT" != "true" ]; then
    _STANDALONE_MLFLOW_POD=$(kubectl get po -n "$NAMESPACE" \
        -l "control-plane=controller-manager,app.kubernetes.io/name=mlflow-operator" \
        --field-selector=status.phase=Running \
        -o jsonpath="{.items[0].metadata.name}" 2>/dev/null || true)

    if [ -n "$_STANDALONE_MLFLOW_POD" ]; then
        echo "🔍 Detected standalone MLflow operator pod ($_STANDALONE_MLFLOW_POD) — skipping CSV patch."
        echo "   Operator is already running; proceeding with --skip-operator."
    else
        echo "🔧 Patching OLM CSV with MLflow operator manifests..."
        if ! find_csv_and_update "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH"; then
            echo "❌ Failed to patch CSV"
            exit 1
        fi
    fi
fi

# ─── Deploy ───────────────────────────────────────────────────────────────────
# deploy.py handles: namespace creation, operator deployment (kustomize path),
# infrastructure (postgres/seaweedfs), credentials secrets, and the MLflow CR.

if [ "$SKIP_DEPLOYMENT" = "true" ]; then
    echo "⏭️  Skipping deployment (SKIP_DEPLOYMENT=true)"
else
    echo "🚀 Deploying MLflow via deploy.py..."

    DEPLOY_ARGS=(
        --namespace             "$NAMESPACE"
        --mlflow-image          "$MLFLOW_RESOLVED_IMAGE"
        --mlflow-operator-image "$MLFLOW_OPERATOR_IMAGE"
    )

    # Operator deployment: skip when OLM manages it, or when explicitly requested
    if [ "$DEPLOY_MLFLOW_OPERATOR" = "true" ] || [ "$SKIP_OPERATOR" = "true" ]; then
        DEPLOY_ARGS+=(--skip-operator)
    fi

    # Infrastructure deployment: skip on OpenShift (pre-existing) or when explicitly requested
    if [ "$SKIP_INFRASTRUCTURE" = "true" ]; then
        DEPLOY_ARGS+=(--skip-infrastructure)
    fi

    # Artifact storage
    case "$STORAGE_TYPE" in
        s3)
            DEPLOY_ARGS+=(--artifact-storage s3)
            [ -n "${AWS_ACCESS_KEY_ID:-}"     ] && DEPLOY_ARGS+=(--s3-access-key "$AWS_ACCESS_KEY_ID")
            [ -n "${AWS_SECRET_ACCESS_KEY:-}" ] && DEPLOY_ARGS+=(--s3-secret-key "$AWS_SECRET_ACCESS_KEY")
            [ -n "${BUCKET:-}"                ] && DEPLOY_ARGS+=(--s3-bucket     "$BUCKET")
            [ -n "${S3_ENDPOINT_URL:-}"       ] && DEPLOY_ARGS+=(--s3-endpoint   "$S3_ENDPOINT_URL")
            ;;
        *)
            DEPLOY_ARGS+=(--artifact-storage file)
            ;;
    esac

    # Metadata store
    case "$DB_TYPE" in
        postgresql|postgres)
            DEPLOY_ARGS+=(--backend-store postgres --registry-store postgres)
            [ -n "${DB_HOST:-}"     ] && DEPLOY_ARGS+=(--postgres-host        "$DB_HOST")
            [ -n "${DB_PORT:-}"     ] && DEPLOY_ARGS+=(--postgres-port        "$DB_PORT")
            [ -n "${DB_USER:-}"     ] && DEPLOY_ARGS+=(--postgres-user        "$DB_USER")
            [ -n "${DB_PASSWORD:-}" ] && DEPLOY_ARGS+=(--postgres-password    "$DB_PASSWORD")
            [ -n "${DB_NAME:-}"     ] && DEPLOY_ARGS+=(--postgres-backend-db  "$DB_NAME"
                                                        --postgres-registry-db "$DB_NAME")
            ;;
        *)
            DEPLOY_ARGS+=(--backend-store sqlite --registry-store sqlite)
            ;;
    esac

    python "$DEPLOY_PY" "${DEPLOY_ARGS[@]}"
fi

# ─── Workspace RBAC ───────────────────────────────────────────────────────────
# The kubernetes-auth backend checks RBAC in the workspace namespace on every
# request, so the MLflow SA must have access in each workspace namespace.
#
# Additionally, the SA needs system:auth-delegator at the cluster level so it
# can perform TokenReview (validate Bearer tokens) — a cluster-scoped operation
# that is not covered by namespace-scoped admin RoleBindings.

echo "🔐 Setting up RBAC for ${MLFLOW_SA_NAME}..."

# Grant cluster-level token validation (TokenReview / SubjectAccessReview)
kubectl create clusterrolebinding "mlflow-auth-delegator-${MLFLOW_NAME}" \
    --clusterrole=system:auth-delegator \
    --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "  Setting up workspace namespaces..."
for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
    ws=$(echo "$ws" | xargs)
    [ -z "$ws" ] && continue

    if ! kubectl get namespace "$ws" &>/dev/null; then
        echo "  Creating namespace: $ws"
        kubectl create namespace "$ws"
        _CREATED_WORKSPACES="${_CREATED_WORKSPACES:+${_CREATED_WORKSPACES},}${ws}"
    fi

    echo "  Granting ${MLFLOW_SA_NAME} admin access in: $ws"
    kubectl create rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
        --clusterrole=admin \
        --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
        -n "$ws" \
        --dry-run=client -o yaml | kubectl apply -f -
done

# Also grant access in the MLflow namespace itself (required by kubernetes-auth)
kubectl create rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
    --clusterrole=admin \
    --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
    -n "$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

# ─── Tracking URI ─────────────────────────────────────────────────────────────

if [ "$IN_CLUSTER_MODE" = "true" ]; then
    export MLFLOW_TRACKING_URI="$API_BASE"
    HEALTH_URL="${API_BASE}/mlflow/health"
else
    echo "🔗 Port-forwarding MLflow service to localhost:8443..."
    kubectl port-forward "svc/${MLFLOW_NAME}" -n "$NAMESPACE" 8443:8443 &
    PF_PID=$!
    sleep 2
    export MLFLOW_TRACKING_URI="https://localhost:8443"
    HEALTH_URL="https://localhost:8443/mlflow/health"
fi
echo "📍 MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI"

# ─── Health check ─────────────────────────────────────────────────────────────

echo "⏳ Waiting for MLflow health endpoint at $HEALTH_URL..."
RETRY=0
MAX_RETRIES=36  # 36 × 5 s = 3 min
until curl -sk --max-time 5 -o /dev/null -w "%{http_code}" "$HEALTH_URL" | grep -qE "^(200|302)$"; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
        echo "❌ MLflow endpoint did not become reachable within timeout"
        exit 1
    fi
    echo "  Attempt $RETRY/$MAX_RETRIES — retrying in 5s..."
    sleep 5
done
echo "✅ MLflow endpoint is reachable"

# ─── Kube token ───────────────────────────────────────────────────────────────

echo "🔑 Generating token for ${MLFLOW_SA_NAME}..."
if ! kube_token=$(kubectl create token "$MLFLOW_SA_NAME" --namespace "$NAMESPACE"); then
    echo "❌ Failed to create token for $MLFLOW_SA_NAME"
    exit 1
fi
export kube_token

# ─── Tests ────────────────────────────────────────────────────────────────────

TEST_RESULTS_DIR="${TEST_RESULTS_DIR:-/tmp/test-results}"
mkdir -p "$TEST_RESULTS_DIR"

echo "🧪 Running integration tests..."
cd "$SCRIPT_DIR/.."
uv run pytest --junit-xml="${TEST_RESULTS_DIR}/test-results.xml"
TEST_EXIT_CODE=$?

if ls "${TEST_RESULTS_DIR}"/*.xml &>/dev/null; then
    echo "📄 JUnit XML reports generated in: $TEST_RESULTS_DIR"
else
    echo "⚠️  No XML reports found in: $TEST_RESULTS_DIR"
fi

exit "$TEST_EXIT_CODE"
