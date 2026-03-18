#!/bin/bash
# run-deploy.sh: Deploy MLflow and run integration tests.
#
# Configured entirely via environment variables (see --help for the full list).
# Delegates cluster-level deployment to deploy.py; on OpenShift/OLM clusters the
# operator is patched via CSV instead (default; set DEPLOY_MLFLOW_OPERATOR=false to skip).
#
# Platform support:
#   OpenShift/OLM:           DEPLOY_MLFLOW_OPERATOR=true (default) — CSV patching via patch-csv.sh
#   Kind/vanilla Kubernetes: DEPLOY_MLFLOW_OPERATOR=false
#
# Multi-suite mode:
#   By default the script runs tests twice — once with file storage and once with S3 —
#   sharing the operator setup, workspace namespaces, and RBAC across both runs.
#   Control which backends run via ARTIFACT_BACKENDS (e.g. ARTIFACT_BACKENDS=file or ARTIFACT_BACKENDS=s3).

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

# ─── Usage ────────────────────────────────────────────────────────────────────

print_usage() {
    cat <<EOF
Usage: $0

All configuration is provided via environment variables.
Variables can also be set in images/.env (sourced automatically).

MLflow image:
  MLFLOW_IMAGE          Full image reference; overrides MLFLOW_TAG when set
  MLFLOW_TAG            Image tag for MLFLOW_IMAGE_REPO (default: master)
  MLFLOW_IMAGE_REPO     Image repository (default: quay.io/opendatahub/mlflow)
  MLFLOW_OPERATOR_IMAGE MLflow operator image (standalone/Kind path only; ODH/RHOAI operator image is hardcoded in the platform binary)

Storage:
  STORAGE_TYPE          Legacy single-suite selector: file|s3|externals3. Prefer ARTIFACT_BACKENDS for
                        multi-suite runs. When STORAGE_TYPE is set and ARTIFACT_BACKENDS is not,
                        ARTIFACT_BACKENDS is derived from STORAGE_TYPE (backward compatibility).
  DB_TYPE               Metadata store backend: sqlite|postgres (default: sqlite)

  AWS_ACCESS_KEY_ID     S3 access key     (when STORAGE_TYPE=s3 or externals3)
  AWS_SECRET_ACCESS_KEY S3 secret key     (when STORAGE_TYPE=s3 or externals3)
  BUCKET                S3 bucket name    (when STORAGE_TYPE=s3 or externals3)
  S3_ENDPOINT_URL       S3 endpoint URL   (when STORAGE_TYPE=s3; omit for real AWS with externals3)
  AWS_DEFAULT_REGION    AWS region        (when STORAGE_TYPE=externals3; optional for s3)

  DB_HOST               PostgreSQL host   (when DB_TYPE=postgres; default: auto)
  DB_PORT               PostgreSQL port   (default: 5432)
  DB_USER               PostgreSQL user   (default: postgres)
  DB_PASSWORD           PostgreSQL password
  DB_NAME               PostgreSQL database name (default: mydatabase)
  DB_SSLMODE            sslmode for the connection URI (default: 'verify-full' with TLS, 'disable' without)

Infrastructure image overrides:
  POSTGRES_IMAGE        PostgreSQL container image override
  SEAWEEDFS_IMAGE       SeaweedFS container image override

TLS (self-deployed infrastructure):
  POSTGRES_TLS          true|false — enable TLS on the self-deployed PostgreSQL server (default: false)
                        When true, sslmode defaults to "verify-full" unless DB_SSLMODE is explicitly set.
  SEAWEEDFS_TLS         true|false — enable TLS on the self-deployed SeaweedFS S3 endpoint (default: false)
                        When true, the S3 endpoint scheme is automatically switched to https://.
                        A self-signed cert is generated on the host via openssl and stored as a K8s Secret.

Operator / OpenShift:
  DEPLOY_MLFLOW_OPERATOR  true|false — patch the OLM CSV instead of deploying via kustomize;
                          use on OpenShift/OLM clusters (default: true)
  MLFLOW_OPERATOR_OWNER   GitHub owner for CSV manifest download (default: opendatahub-io)
  MLFLOW_OPERATOR_REPO    GitHub repo for CSV manifest download  (default: mlflow-operator)
  MLFLOW_OPERATOR_BRANCH  GitHub branch for CSV manifest download (default: main)
  INFRASTRUCTURE_PLATFORM Infrastructure overlay: base|openshift
                          (default: openshift when DEPLOY_MLFLOW_OPERATOR=true, else base)

Skip / control flags:
  SKIP_DEPLOYMENT       true|false — skip all cluster deployment (default: false)
  SKIP_OPERATOR         true|false — skip operator deployment only (default: false)
  SKIP_INFRASTRUCTURE   true|false — skip PostgreSQL/SeaweedFS deployment (default: false)
  SKIP_CLEANUP          true|false — leave resources in place after the run (default: false)
  FAIL_FAST             true|false — stop after the first backend suite failure (default: true)
                        Set to false to run all backends even if one fails.

Other:
  NAMESPACE             Target namespace (default: opendatahub)
  MLFLOW_SA_NAME        Service account name created by the operator (default: mlflow-sa)
  IN_CLUSTER_MODE       true|false — false enables port-forwarding for local runs (default: true)
  workspaces            Comma-separated workspace namespace list (default: two random names)
  TEST_RESULTS_DIR      Directory for JUnit XML output (default: /mlflow/results)
  DEPLOY_PY             Path to deploy.py (default: <repo>/.github/actions/deploy/deploy.py)
  ARTIFACT_BACKENDS     Comma-separated artifact storage backends to test in sequence (default: file,s3)
                        Supported values: file, s3 (SeaweedFS), externals3 (external S3 via AWS_* env vars)
                        Each backend deploys a fresh MLflow CR, runs the full test suite, then
                        removes the CR before the next backend runs.
                        The operator, workspace namespaces, and RBAC are shared across all backends.

Positional arguments:
  Any arguments after the script name are forwarded verbatim to pytest.
  e.g. bash test-run.sh -m smoke
       bash test-run.sh -m "smoke or integration"
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    print_usage; exit 0
fi
# Any positional arguments are forwarded verbatim to pytest (e.g. "-m smoke").
PYTEST_ARGS=("$@")

# ─── Defaults ─────────────────────────────────────────────────────────────────

NAMESPACE="${NAMESPACE:-opendatahub}"
MLFLOW_NAME="mlflow"
# SA name is set by the operator's Helm chart; see internal/controller/constants.go
MLFLOW_SA_NAME="${MLFLOW_SA_NAME:-mlflow-sa}"

MLFLOW_TAG="${MLFLOW_TAG:-master}"
MLFLOW_IMAGE_REPO="${MLFLOW_IMAGE_REPO:-}"
MLFLOW_IMAGE="${MLFLOW_IMAGE:-}"
MLFLOW_OPERATOR_IMAGE="${MLFLOW_OPERATOR_IMAGE:-quay.io/opendatahub/mlflow-operator:odh-stable}"

DB_TYPE="${DB_TYPE:-sqlite}"

# When true (default) the script patches the OLM CSV instead of deploying the operator via kustomize
# and passes --skip-operator to deploy.py. Infrastructure is NOT automatically skipped —
# set SKIP_INFRASTRUCTURE=true separately if infra is pre-existing.
DEPLOY_MLFLOW_OPERATOR="${DEPLOY_MLFLOW_OPERATOR:-true}"
MLFLOW_OPERATOR_OWNER="${MLFLOW_OPERATOR_OWNER:-opendatahub-io}"
MLFLOW_OPERATOR_REPO="${MLFLOW_OPERATOR_REPO:-mlflow-operator}"
MLFLOW_OPERATOR_BRANCH="${MLFLOW_OPERATOR_BRANCH:-main}"

SKIP_DEPLOYMENT="${SKIP_DEPLOYMENT:-false}"
SKIP_OPERATOR="${SKIP_OPERATOR:-false}"
SKIP_INFRASTRUCTURE="${SKIP_INFRASTRUCTURE:-false}"
SKIP_CLEANUP="${SKIP_CLEANUP:-false}"
FAIL_FAST="${FAIL_FAST:-true}"
IN_CLUSTER_MODE="${IN_CLUSTER_MODE:-true}"

# Suites to run.  Each entry is an artifact storage backend (file|s3); the script
# deploys a fresh MLflow CR per suite, runs the full test suite, then tears it down.
# Backward compatibility: STORAGE_TYPE=<type> (old single-suite interface) is honoured
# when ARTIFACT_BACKENDS is not explicitly set.
ARTIFACT_BACKENDS="${ARTIFACT_BACKENDS:-${STORAGE_TYPE:-file,s3}}"
# STORAGE_TYPE is set per-iteration by the main loop; this default is only used if
# run_suite is somehow called outside the loop (e.g. during development/debugging).
STORAGE_TYPE="${STORAGE_TYPE:-file}"

# Platform for infrastructure overlays: base|openshift.
# Defaults to openshift when the cluster has the OpenShift API (oc/routes available),
# otherwise falls back to base. Can always be overridden explicitly.
if [ -z "${INFRASTRUCTURE_PLATFORM:-}" ]; then
    if kubectl api-resources --api-group=route.openshift.io &>/dev/null 2>&1; then
        INFRASTRUCTURE_PLATFORM="openshift"
    else
        INFRASTRUCTURE_PLATFORM="base"
    fi
fi

# Infrastructure image overrides
POSTGRES_IMAGE="${POSTGRES_IMAGE:-}"
SEAWEEDFS_IMAGE="${SEAWEEDFS_IMAGE:-}"

# TLS flags for self-deployed infrastructure
POSTGRES_TLS="${POSTGRES_TLS:-false}"
SEAWEEDFS_TLS="${SEAWEEDFS_TLS:-false}"

# PostgreSQL sslmode appended to the connection URI.
# Leave empty to let deploy.py use its default ("disable" for self-deployed postgres).
DB_SSLMODE="${DB_SSLMODE:-}"

RANDOM_SUFFIX=$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)
WORKSPACE_LIST="${workspaces:-workspace1-${RANDOM_SUFFIX},workspace2-${RANDOM_SUFFIX}}"
# Export so pytest (Config.WORKSPACES) reads the same names RBAC is set up for
export workspaces="$WORKSPACE_LIST"

PF_PID=""
_CREATED_WORKSPACES=""  # tracks only namespaces created by this run (not pre-existing)
# Set to true after the first suite so subsequent suites skip re-deploying the operator.
_OPERATOR_DEPLOYED=false

MLFLOW_DEFAULT_IMAGE=""
if [ -n "${MLFLOW_IMAGE_REPO:-}" ] && [ -n "${MLFLOW_TAG:-}" ]; then
    MLFLOW_DEFAULT_IMAGE="${MLFLOW_IMAGE_REPO}:${MLFLOW_TAG}"
fi
MLFLOW_RESOLVED_IMAGE="${MLFLOW_IMAGE:-${MLFLOW_DEFAULT_IMAGE}}"



API_BASE="https://${MLFLOW_NAME}.${NAMESPACE}.svc.cluster.local:8443"

# ─── Shared teardown (EXIT trap) ──────────────────────────────────────────────
# Removes all resources created by this run: workspace namespaces (only those the
# script itself created, not pre-existing ones), role bindings, the MLflow CR,
# and any self-deployed infrastructure (PostgreSQL, SeaweedFS).
# The DataScienceCluster mlflowoperator component is assumed to remain Managed.

cleanup() {
    [ -n "$PF_PID" ] && kill -0 "$PF_PID" 2>/dev/null && kill "$PF_PID"

    # Only clean up resources this run created. When SKIP_DEPLOYMENT=true the
    # script was pointed at a pre-existing environment and must not disturb it.
    if [ "$SKIP_DEPLOYMENT" != "true" ]; then
        for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
            ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
            kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" -n "$ws" --ignore-not-found 2>/dev/null || true
        done
        # Only delete namespaces this run created; pre-existing namespaces are left intact.
        for ws in $(echo "$_CREATED_WORKSPACES" | tr ',' ' '); do
            ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
            kubectl delete namespace "$ws" --ignore-not-found 2>/dev/null || true
        done
        kubectl delete mlflow "$MLFLOW_NAME" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
        kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrolebinding "mlflow-auth-delegator-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrolebinding "mlflow-config-view-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrole "mlflow-config-reader-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true

        if [ "$SKIP_INFRASTRUCTURE" != "true" ]; then
            # Compute per-component overlay directories, accounting for TLS overlays.
            local postgres_overlay seaweedfs_overlay
            if [ "${POSTGRES_TLS:-false}" = "true" ]; then
                [ "$INFRASTRUCTURE_PLATFORM" = "openshift" ] && postgres_overlay="openshift-tls" || postgres_overlay="tls"
            else
                postgres_overlay="$INFRASTRUCTURE_PLATFORM"
            fi
            if [ "${SEAWEEDFS_TLS:-false}" = "true" ]; then
                [ "$INFRASTRUCTURE_PLATFORM" = "openshift" ] && seaweedfs_overlay="openshift-tls" || seaweedfs_overlay="tls"
            else
                seaweedfs_overlay="$INFRASTRUCTURE_PLATFORM"
            fi

            echo "  Removing self-deployed infrastructure..."
            kustomize build "$REPO_ROOT/config/postgres/$postgres_overlay" \
                | kubectl delete --ignore-not-found -n "$NAMESPACE" -f - 2>/dev/null || true
            # Only tear down SeaweedFS if the in-cluster s3 backend was used.
            # externals3 uses an external S3 service that this run did not deploy.
            if echo "$ARTIFACT_BACKENDS" | tr ',' '\n' | grep -qxF 's3'; then
                export APPLICATION_CRD_ID=mlflow-pipelines \
                       PROFILE_NAMESPACE_LABEL=mlflow-profile \
                       S3_BUCKET="${BUCKET:-mlpipeline}"
                kustomize build "$REPO_ROOT/config/seaweedfs/$seaweedfs_overlay" \
                    | envsubst '$NAMESPACE,$APPLICATION_CRD_ID,$PROFILE_NAMESPACE_LABEL,$S3_BUCKET' \
                    | kubectl delete --ignore-not-found -f - 2>/dev/null || true
            fi

            # Clean up TLS resources (cert Secrets, CA bundle ConfigMap, DSCI restore)
            # after infrastructure is torn down to avoid noisy pod errors from missing secrets.
            if [ "${POSTGRES_TLS:-false}" = "true" ] || [ "${SEAWEEDFS_TLS:-false}" = "true" ]; then
                local _tls_args=(--namespace "$NAMESPACE")
                [ "${POSTGRES_TLS:-false}"  = "true" ] && _tls_args+=(--postgres-tls)
                [ "${SEAWEEDFS_TLS:-false}" = "true" ] && _tls_args+=(--seaweedfs-tls)
                uv run python3 "$DEPLOY_PY" --cleanup-tls "${_tls_args[@]}" 2>/dev/null || true
            fi
        fi
    fi
}

if [ "$SKIP_CLEANUP" != "true" ]; then
    trap cleanup EXIT
fi

# ─── CSV patching (OpenShift/OLM) ─────────────────────────────────────────────
# Done once before the suite loop — the MLflow operator manifests don't change
# between suites, so there is no need to re-patch the CSV for each storage type.
# This path applies only when the MLflow operator is embedded inside a platform
# operator (ODH/RHOAI). When the MLflow operator runs standalone
# (mlflow-operator-controller-manager), the CSV patch is skipped automatically.

if [ "$DEPLOY_MLFLOW_OPERATOR" = "true" ] && [ "$SKIP_DEPLOYMENT" != "true" ]; then
    echo "Patching OLM CSV with MLflow operator manifests..."
    if ! find_csv_and_update "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH"; then
        echo "ERROR: Failed to patch CSV" >&2
        exit 1
    fi
    _OPERATOR_DEPLOYED=true
fi

# ─── Suite runner ─────────────────────────────────────────────────────────────

setup_rbac() {
    # The kubernetes-auth backend checks RBAC in the workspace namespace on every
    # request, so the MLflow SA must have access in each workspace namespace.
    # Additionally, the SA needs system:auth-delegator at the cluster level so it
    # can perform TokenReview — a cluster-scoped operation not covered by
    # namespace-scoped admin RoleBindings.
    #
    # Called at the start of each suite because the operator recreates the SA when
    # the MLflow CR is (re)applied, so role bindings may need to be reapplied.
    echo "  Setting up RBAC for ${MLFLOW_SA_NAME}..."

    kubectl create clusterrolebinding "mlflow-auth-delegator-${MLFLOW_NAME}" \
        --clusterrole=system:auth-delegator \
        --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
        --dry-run=client -o yaml | kubectl apply -f -

    # Grant cluster-wide list/watch on mlflowconfigs so the MLflow server can look up
    # namespace-specific artifact storage configs. The operator's Helm chart creates a
    # ClusterRoleBinding for this, but in the CSV-patch path OLM may block ClusterRole
    # creation. Create a self-contained ClusterRole here so we don't depend on
    # mlflow-view (which may or may not exist) being present in the cluster.
    kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: mlflow-config-reader-${MLFLOW_NAME}
rules:
  - apiGroups: ["mlflow.kubeflow.org"]
    resources: ["mlflowconfigs"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "watch"]
EOF
    kubectl create clusterrolebinding "mlflow-config-view-${MLFLOW_NAME}" \
        --clusterrole="mlflow-config-reader-${MLFLOW_NAME}" \
        --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
        --dry-run=client -o yaml | kubectl apply -f -

    for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs)
        [ -z "$ws" ] && continue
        kubectl create rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
            --clusterrole=admin \
            --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
            -n "$ws" \
            --dry-run=client -o yaml | kubectl apply -f -
    done

    kubectl create rolebinding "mlflow-permissions-${MLFLOW_NAME}" \
        --clusterrole=admin \
        --serviceaccount="${NAMESPACE}:${MLFLOW_SA_NAME}" \
        -n "$NAMESPACE" \
        --dry-run=client -o yaml | kubectl apply -f -
}

run_suite() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Suite: storage=${STORAGE_TYPE} db=${DB_TYPE}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # ── Workspace namespaces (idempotent) ────────────────────────────────────────
    for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
        if ! kubectl get namespace "$ws" &>/dev/null; then
            kubectl create namespace "$ws" || return $?
            _CREATED_WORKSPACES="${_CREATED_WORKSPACES:+${_CREATED_WORKSPACES},}${ws}"
        fi
    done

    # ── Deploy ──────────────────────────────────────────────────────────────────
    if [ "$SKIP_DEPLOYMENT" = "true" ]; then
        echo "  Skipping deployment (SKIP_DEPLOYMENT=true)"
    else
        echo "  Deploying MLflow (storage=${STORAGE_TYPE}) via deploy.py..."

        local deploy_args=(
            --namespace             "$NAMESPACE"
            --mlflow-operator-image "$MLFLOW_OPERATOR_IMAGE"
            --platform              "$INFRASTRUCTURE_PLATFORM"
        )
        [ -n "${MLFLOW_RESOLVED_IMAGE}" ] && deploy_args+=(--mlflow-image "$MLFLOW_RESOLVED_IMAGE")

        [ -n "${POSTGRES_IMAGE:-}"  ] && deploy_args+=(--postgres-image  "$POSTGRES_IMAGE")
        [ -n "${SEAWEEDFS_IMAGE:-}" ] && deploy_args+=(--seaweedfs-image "$SEAWEEDFS_IMAGE")
        [ -n "${DB_SSLMODE:-}"      ] && deploy_args+=(--postgres-sslmode "$DB_SSLMODE")
        [ "${POSTGRES_TLS:-false}"  = "true" ] && deploy_args+=(--postgres-tls)
        [ "${SEAWEEDFS_TLS:-false}" = "true" ] && deploy_args+=(--seaweedfs-tls)

        # Skip operator when OLM manages it, when explicitly requested, or when it
        # was already deployed by a previous suite in this run.
        if [ "$DEPLOY_MLFLOW_OPERATOR" = "true" ] || \
           [ "$SKIP_OPERATOR" = "true" ] || \
           [ "$_OPERATOR_DEPLOYED" = "true" ]; then
            deploy_args+=(--skip-operator)
        fi

        if [ "$SKIP_INFRASTRUCTURE" = "true" ]; then
            deploy_args+=(--skip-infrastructure)
        fi

        case "$STORAGE_TYPE" in
            s3)
                deploy_args+=(--artifact-storage s3)
                [ -n "${AWS_ACCESS_KEY_ID:-}"     ] && deploy_args+=(--s3-access-key "$AWS_ACCESS_KEY_ID")
                [ -n "${AWS_SECRET_ACCESS_KEY:-}" ] && deploy_args+=(--s3-secret-key "$AWS_SECRET_ACCESS_KEY")
                [ -n "${BUCKET:-}"                ] && deploy_args+=(--s3-bucket     "$BUCKET")
                [ -n "${S3_ENDPOINT_URL:-}"       ] && deploy_args+=(--s3-endpoint   "$S3_ENDPOINT_URL")
                ;;
            externals3)
                # Use externally-provided S3 credentials (AWS_* env vars); SeaweedFS is
                # NOT deployed. Requires: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BUCKET.
                # Optional: S3_ENDPOINT_URL (omit for real AWS), AWS_DEFAULT_REGION.
                deploy_args+=(--artifact-storage externals3)
                deploy_args+=(--s3-access-key "$AWS_ACCESS_KEY_ID")
                deploy_args+=(--s3-secret-key "$AWS_SECRET_ACCESS_KEY")
                deploy_args+=(--s3-bucket     "$BUCKET")
                [ -n "${S3_ENDPOINT_URL:-}"    ] && deploy_args+=(--s3-endpoint "$S3_ENDPOINT_URL")
                [ -n "${AWS_DEFAULT_REGION:-}" ] && deploy_args+=(--s3-region   "$AWS_DEFAULT_REGION")
                ;;
            file)
                deploy_args+=(--artifact-storage file)
                ;;
            *)
                echo "ERROR: Unsupported ARTIFACT_BACKENDS value: '${STORAGE_TYPE}'. Supported: file, s3, externals3" >&2
                return 1
                ;;
        esac

        case "$DB_TYPE" in
            postgresql|postgres)
                deploy_args+=(--backend-store postgres --registry-store postgres)
                [ -n "${DB_HOST:-}"     ] && deploy_args+=(--postgres-host        "$DB_HOST")
                [ -n "${DB_PORT:-}"     ] && deploy_args+=(--postgres-port        "$DB_PORT")
                [ -n "${DB_USER:-}"     ] && deploy_args+=(--postgres-user        "$DB_USER")
                [ -n "${DB_PASSWORD:-}" ] && deploy_args+=(--postgres-password    "$DB_PASSWORD")
                [ -n "${DB_NAME:-}"     ] && deploy_args+=(--postgres-backend-db  "$DB_NAME"
                                                            --postgres-registry-db "$DB_NAME")
                ;;
            *)
                deploy_args+=(--backend-store sqlite --registry-store sqlite)
                ;;
        esac

        uv run --no-sync "$DEPLOY_PY" "${deploy_args[@]}" || return $?
        _OPERATOR_DEPLOYED=true
    fi

    # ── Between-suite teardown (runs on every exit path) ────────────────────────
    # Registered here so it fires even when RBAC, health-check, or token steps fail,
    # ensuring the port-forward and MLflow CR are cleaned up before the next suite.
    # SKIP_CLEANUP only suppresses the final EXIT trap, not this per-suite reset.
    local _suite_teardown_done=false
    _suite_teardown() {
        "$_suite_teardown_done" && return
        _suite_teardown_done=true
        [ -n "$PF_PID" ] && kill -0 "$PF_PID" 2>/dev/null && kill "$PF_PID" && PF_PID=""
        # --wait ensures finalizers have completed before the next suite's deploy.py apply.
        kubectl delete mlflow "$MLFLOW_NAME" -n "$NAMESPACE" --ignore-not-found --wait --timeout=120s 2>/dev/null || true
    }
    trap _suite_teardown RETURN

    # ── RBAC ────────────────────────────────────────────────────────────────────
    # Applied after deploy.py so the SA exists; runs before tests execute.
    setup_rbac || return $?

    # ── Tracking URI ────────────────────────────────────────────────────────────
    if [ "$IN_CLUSTER_MODE" = "true" ]; then
        export MLFLOW_TRACKING_URI="$API_BASE"
        local health_url="${API_BASE}/mlflow/health"
    else
        echo "  Port-forwarding MLflow service to localhost:8443..."
        kubectl port-forward "svc/${MLFLOW_NAME}" -n "$NAMESPACE" 8443:8443 &
        PF_PID=$!
        sleep 2
        export MLFLOW_TRACKING_URI="https://localhost:8443"
        local health_url="https://localhost:8443/mlflow/health"
    fi
    echo "  MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI"

    # ── Health check ────────────────────────────────────────────────────────────
    echo "  Waiting for MLflow health endpoint at $health_url..."
    local retry=0
    local max_retries=36  # 36 × 5 s = 3 min
    until curl -sk --max-time 5 -o /dev/null -w "%{http_code}" "$health_url" | grep -qE "^(200|302)$"; do
        retry=$((retry + 1))
        if [ "$retry" -ge "$max_retries" ]; then
            echo "ERROR: MLflow endpoint did not become reachable within timeout" >&2
            return 1
        fi
        echo "  Attempt $retry/$max_retries — retrying in 5s..."
        sleep 5
    done
    echo "  MLflow endpoint is reachable"

    # ── Kube token ──────────────────────────────────────────────────────────────
    echo "  Generating token for ${MLFLOW_SA_NAME}..."
    if ! kube_token=$(kubectl create token "$MLFLOW_SA_NAME" --namespace "$NAMESPACE"); then
        echo "ERROR: Failed to create token for $MLFLOW_SA_NAME" >&2
        return 1
    fi
    export kube_token

    # ── Tests ───────────────────────────────────────────────────────────────────
    # Export artifact_storage and serve_artifacts so Config reads in the test suite
    # match what was actually deployed. Both s3 and externals3 are S3-compatible
    # from the test perspective, so normalise externals3 → s3.
    case "$STORAGE_TYPE" in
        s3|externals3) export artifact_storage="s3" ;;
        *)             export artifact_storage="$STORAGE_TYPE" ;;
    esac
    # deploy.py defaults --serve-artifacts to "true"; export the same default so
    # Config.SERVE_ARTIFACTS stays in sync if the default ever changes.
    export serve_artifacts="${SERVE_ARTIFACTS:-true}"

    local results_file="${TEST_RESULTS_DIR}/xunit_report_${STORAGE_TYPE}.xml"
    echo "  Running tests (output: $results_file)..."
    cd "$SCRIPT_DIR/.."
    local suite_exit=0
    uv run --no-sync pytest --junit-xml="$results_file" "${PYTEST_ARGS[@]}" || suite_exit=$?
    cd "$SCRIPT_DIR"

    return "$suite_exit"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

TEST_RESULTS_DIR="${TEST_RESULTS_DIR:-/mlflow/results}"
mkdir -p "$TEST_RESULTS_DIR"

OVERALL_EXIT=0
for STORAGE_TYPE in $(echo "$ARTIFACT_BACKENDS" | tr ',' ' '); do
    STORAGE_TYPE=$(echo "$STORAGE_TYPE" | xargs)
    [ -z "$STORAGE_TYPE" ] && continue
    if ! run_suite; then
        OVERALL_EXIT=1
        [ "$FAIL_FAST" = "true" ] && break
    fi
done

echo ""
if ls "${TEST_RESULTS_DIR}"/*.xml &>/dev/null; then
    echo "JUnit XML reports generated in: $TEST_RESULTS_DIR"
    ls "${TEST_RESULTS_DIR}"/*.xml
else
    echo "WARNING: No XML reports found in: $TEST_RESULTS_DIR" >&2
fi

exit "$OVERALL_EXIT"
