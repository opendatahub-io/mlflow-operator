#!/bin/bash
# test-run.sh: Deploy MLflow and run integration tests.
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
TEST_INFRA_ROOT="${TEST_INFRA_ROOT:-$REPO_ROOT/.github/test-infra}"
UV_PROJECT_DIR="${UV_PROJECT_DIR:-$REPO_ROOT/mlflow-tests}"
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
  BACKEND_STORE         Backend store backend: sqlite|postgres (default: sqlite)
  REGISTRY_STORE        Registry store backend: sqlite|postgres (default: sqlite)

  AWS_ACCESS_KEY_ID     S3 access key     (when STORAGE_TYPE=s3 or externals3)
  AWS_SECRET_ACCESS_KEY S3 secret key     (when STORAGE_TYPE=s3 or externals3)
  BUCKET                S3 bucket name    (when STORAGE_TYPE=s3 or externals3)
  S3_ENDPOINT_URL       S3 endpoint URL   (when STORAGE_TYPE=s3 or externals3)
                        Falls back to AWS_DEFAULT_ENDPOINT when not explicitly set.
  AWS_DEFAULT_ENDPOINT  Alias for S3_ENDPOINT_URL (set by Jenkins)
  AWS_DEFAULT_REGION    AWS region        (when STORAGE_TYPE=externals3; optional for s3)

  DB_HOST               PostgreSQL host   (when BACKEND_STORE=postgres and/or REGISTRY_STORE=postgres; default: auto)
  DB_PORT               PostgreSQL port   (default: 5432)
  DB_USER               PostgreSQL user   (default: mlflow; custom values require
                        reused/external PostgreSQL via SKIP_INFRASTRUCTURE=true)
  DB_PASSWORD           PostgreSQL password (custom values require reused/external
                        PostgreSQL via SKIP_INFRASTRUCTURE=true)
  DB_NAME               PostgreSQL database name (default: mydatabase; custom
                        values require reused/external PostgreSQL via
                        SKIP_INFRASTRUCTURE=true)
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
  CA_BUNDLE_PATH        Path to a PEM CA bundle file (for externally-provided TLS storage).
                        Mutually exclusive with CA_BUNDLE_CONFIGMAP.
  CA_BUNDLE_CONFIGMAP   Name of an existing ConfigMap containing the CA bundle.
                        Mutually exclusive with CA_BUNDLE_PATH.

Operator / OpenShift:
  DEPLOY_MLFLOW_OPERATOR  true|false — patch the OLM CSV instead of deploying via kustomize;
                          use on OpenShift/OLM clusters (default: true)
  MLFLOW_OPERATOR_OWNER   GitHub owner for CSV manifest download (default: opendatahub-io)
  MLFLOW_OPERATOR_REPO    GitHub repo for CSV manifest download  (default: mlflow-operator)
  MLFLOW_OPERATOR_BRANCH  GitHub branch for CSV manifest download (default: main)
  INFRASTRUCTURE_PLATFORM Infrastructure overlay: base|openshift
                          (default: auto-detect OpenShift via route.openshift.io, else base)
  FORCE_PORT_FORWARD      true|false — always port-forward the MLflow service to localhost,
                          even on OpenShift (default: false)
  ARTIFACTS_SERVER        true|false — enable and test the dedicated metadata-aware artifact
                          server (default: false). Requires the HTTPRoute API, PostgreSQL
                          backend/registry stores, and file, s3, or externals3 artifacts.
                          Normal runs may exercise multiple artifact backends. Generic Kubernetes
                          accesses the artifact Service through localhost:8444.
  ARTIFACTS_SERVER_GATEWAY true|false — validate live Gateway route acceptance and rewrites
                          (default: false). Requires ARTIFACTS_SERVER=true and OpenShift.

Skip / control flags:
  SKIP_DEPLOYMENT       true|false — skip all cluster deployment (default: false).
                        Requires exactly one backend matching the reused MLflow CR.
  SKIP_OPERATOR         true|false — skip operator deployment only (default: false)
  SKIP_INFRASTRUCTURE   true|false — skip PostgreSQL/SeaweedFS deployment (default: false)
  SKIP_CLEANUP          true|false — leave resources in place after the run (default: false).
                        Requires exactly one backend value; use ARTIFACT_BACKENDS=file
                        or STORAGE_TYPE=file (or another single backend) when preserving
                        a deployment for later inspection or reuse. The default path
                        deletes the cluster-scoped MLflow CR after every suite,
                        including the last one, so leftover instances cannot block
                        MLflowOperator removal.
  CLEANUP_REUSED_RESOURCES true|false|on_success — when SKIP_DEPLOYMENT=true
                        and SKIP_CLEANUP=false, also remove the reused MLflow
                        CR, harness-managed RBAC, and any self-deployed
                        PostgreSQL / SeaweedFS infrastructure implied by the
                        current env vars. Use on_success to preserve resources
                        after failures but clean them up after successful runs
                        (default: false)
  FAIL_FAST             true|false — stop after the first backend suite failure (default: true)
                        Set to false to run all backends even if one fails.

Other:
  NAMESPACE             Target namespace (default: opendatahub)
  MLFLOW_SA_NAME        Service account name created by the operator (default: mlflow-sa)
  TRACE_ARCHIVAL_RETENTION
                        Retention configured on spec.traceArchival for s3/externals3
                        deploys with PostgreSQL backend and registry stores (default:
                        1m so semantic archival smoke coverage can archive fresh traces)
  workspaces            Comma-separated workspace namespace list (default: two random names)
  upgrade_test_workspace Static workspace namespace for upgrade pytest phases. During
                        upgrade-phase runs, the harness derives workspaces and RBAC
                        setup from this namespace automatically.
  TEST_RESULTS_DIR      Directory for JUnit XML output (default: /mlflow/results).
                        Pytest writes xunit_report_<storage>.xml here. Pre-pytest
                        harness aborts write the same filename so Jenkins/Report
                        Portal still see a failed mlflow-e2e testcase.
  DEPLOY_PY             Path to deploy.py (default: <repo>/.github/actions/deploy/deploy.py)
  ARTIFACT_BACKENDS     Comma-separated artifact storage backends to test in sequence (default: file,s3)
                        Supported values: file, s3 (SeaweedFS), externals3 (external S3 via AWS_* env vars)
                        Each backend deploys a fresh MLflow CR, runs the full test suite, then
                        removes the CR before the next backend runs.
                        The operator, workspace namespaces, and RBAC are shared across all backends.
                        Upgrade phases require exactly one backend value.

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

infer_requested_upgrade_phase() {
    local mark_expression="${1:-}"
    case "${mark_expression}" in
        pre_upgrade) printf 'pre_upgrade' ;;
        post_upgrade) printf 'post_upgrade' ;;
        *) printf '' ;;
    esac
}

INFERRED_UPGRADE_PHASE=""
JUNIT_SUITE_NAME="mlflow-e2e"
for ((i=0; i<${#PYTEST_ARGS[@]}; i++)); do
    arg="${PYTEST_ARGS[$i]}"
    mark_expr=""
    if [[ "$arg" == *junit_suite_name=* ]]; then
        JUNIT_SUITE_NAME="${arg#*junit_suite_name=}"
        JUNIT_SUITE_NAME="${JUNIT_SUITE_NAME%% *}"
    elif [ "$arg" = "-o" ]; then
        next_index=$((i + 1))
        if [ "$next_index" -lt "${#PYTEST_ARGS[@]}" ]; then
            next_opt="${PYTEST_ARGS[$next_index]}"
            if [[ "$next_opt" == junit_suite_name=* ]]; then
                JUNIT_SUITE_NAME="${next_opt#junit_suite_name=}"
            fi
        fi
    fi
    if [[ "$arg" == -m=* ]]; then
        mark_expr="${arg#-m=}"
    elif [[ "$arg" == --markexpr=* ]]; then
        mark_expr="${arg#--markexpr=}"
    elif [ "$arg" = "-m" ] || [ "$arg" = "--markexpr" ]; then
        next_index=$((i + 1))
        if [ "$next_index" -lt "${#PYTEST_ARGS[@]}" ]; then
            mark_expr="${PYTEST_ARGS[$next_index]}"
            INFERRED_UPGRADE_PHASE="$(infer_requested_upgrade_phase "$mark_expr")"
        fi
        continue
    fi

    if [ -n "$mark_expr" ]; then
        INFERRED_UPGRADE_PHASE="$(infer_requested_upgrade_phase "$mark_expr")"
    fi
done

# Create the results dir before any early exit so config/CSV/deploy aborts can
# still emit JUnit XML into the directory Jenkins archives.
TEST_RESULTS_DIR="${TEST_RESULTS_DIR:-/mlflow/results}"
mkdir -p "$TEST_RESULTS_DIR"

harness_junit_path() {
    if [ -n "${STORAGE_TYPE:-}" ]; then
        printf '%s\n' "${TEST_RESULTS_DIR}/xunit_report_${STORAGE_TYPE}.xml"
    else
        printf '%s\n' "${TEST_RESULTS_DIR}/xunit_report.xml"
    fi
}

harness_error_body() {
    local message="$1"
    printf '%s\n' "$message"
    printf 'storage=%s backend=%s registry=%s\n' \
        "${STORAGE_TYPE:-unset}" "${BACKEND_STORE:-unset}" "${REGISTRY_STORE:-unset}"
    printf 'namespace=%s\n' "${NAMESPACE:-unset}"
    if [ -n "${MLFLOW_TRACKING_URI:-}" ]; then
        printf 'MLFLOW_TRACKING_URI=%s\n' "$MLFLOW_TRACKING_URI"
    fi
}

# Compact cluster/HTTP snapshot for Jenkins Test Result. Full pod logs still go
# through collect-debug-logs.sh into TEST_RESULTS_DIR/debug/.
capture_failure_snapshot() {
    local reason="${1:-harness failure}"
    local snapshot_dir="${TEST_RESULTS_DIR}/debug"
    local snapshot_file="${snapshot_dir}/failure-snapshot.txt"
    mkdir -p "$snapshot_dir" || return 0

    {
        echo "=== harness failure snapshot ==="
        echo "reason: ${reason}"
        echo "time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "storage=${STORAGE_TYPE:-unset} backend=${BACKEND_STORE:-unset} registry=${REGISTRY_STORE:-unset}"
        echo "namespace=${NAMESPACE:-unset}"
        echo "MLFLOW_TRACKING_URI=${MLFLOW_TRACKING_URI:-unset}"
        echo
        echo "--- deployments ---"
        kubectl get deploy -n "${NAMESPACE:-}" -o wide 2>&1 || true
        echo
        echo "--- pods ---"
        kubectl get pods -n "${NAMESPACE:-}" -o wide 2>&1 || true
        echo
        echo "--- mlflow CR ---"
        if [ -n "${NAMESPACE:-}" ] && kubectl get mlflow "${MLFLOW_NAME:-mlflow}" -n "$NAMESPACE" >/dev/null 2>&1; then
            kubectl get mlflow "${MLFLOW_NAME:-mlflow}" -n "$NAMESPACE" \
                -o jsonpath='url={.status.url}{"\n"}version={.status.version}{"\n"}{range .status.conditions[*]}{.type}={.status} reason={.reason} message={.message}{"\n"}{end}' 2>&1 || true
            echo
        else
            echo "MLflow CR not found (or kubectl/namespace unavailable)"
        fi
        echo "--- warning events ---"
        kubectl get events -n "${NAMESPACE:-}" --field-selector type=Warning --sort-by=.lastTimestamp 2>&1 | tail -n 20 || true
        echo
        if [ -f "${snapshot_dir}/server-info-last-stderr.txt" ] || [ -f "${snapshot_dir}/server-info-last-body.txt" ]; then
            echo "--- last server-info probe ---"
            echo "stderr: $(tr '\n' ' ' < "${snapshot_dir}/server-info-last-stderr.txt" 2>/dev/null | head -c 300 || true)"
            echo "body:"
            head -c 500 "${snapshot_dir}/server-info-last-body.txt" 2>/dev/null || true
            echo
        fi
    } > "$snapshot_file" || true

    echo "  Failure snapshot: ${snapshot_file}"
    if [ -f "$snapshot_file" ]; then
        cat "$snapshot_file" || true
    fi
}

write_harness_junit_error() {
    local test_name="$1"
    local message="$2"
    local output_file body snapshot_file
    output_file="$(harness_junit_path)"
    body="$(harness_error_body "$message")"
    snapshot_file="${TEST_RESULTS_DIR}/debug/failure-snapshot.txt"
    if [ -f "$snapshot_file" ]; then
        body="${body}"$'\n\n'"$(head -c 8192 "$snapshot_file" 2>/dev/null || true)"
    fi
    if ! python3 "$SCRIPT_DIR/write_harness_junit.py" \
        --output "$output_file" \
        --suite "$JUNIT_SUITE_NAME" \
        --name "$test_name" \
        --message "$message" \
        --body "$body"; then
        echo "WARN: failed to write harness JUnit report to ${output_file}" >&2
        return 0
    fi
}

fail_suite() {
    capture_failure_snapshot "$2"
    write_harness_junit_error "$1" "$2"
}

fail_run() {
    capture_failure_snapshot "$2"
    write_harness_junit_error "$1" "$2"
    exit 1
}

get_supported_mlflow_version_raw() {
    python3 "$REPO_ROOT/scripts/print_supported_mlflow_version.py" \
        --component-metadata "$REPO_ROOT/config/component_metadata.yaml"
}

if [ -z "${MLFLOW_TEST_SUPPORTED_VERSION:-}" ]; then
    MLFLOW_TEST_SUPPORTED_VERSION="$(
        python3 "$REPO_ROOT/scripts/print_supported_mlflow_version.py" \
            --component-metadata "$REPO_ROOT/config/component_metadata.yaml" \
            --normalized
    )"
    export MLFLOW_TEST_SUPPORTED_VERSION
fi

SUPPORTED_MLFLOW_VERSION_RAW="${SUPPORTED_MLFLOW_VERSION_RAW:-$(get_supported_mlflow_version_raw)}"

# ─── Defaults ─────────────────────────────────────────────────────────────────

NAMESPACE="${NAMESPACE:-opendatahub}"
# RHOAI deployments use `redhat-ods-applications` instead of `opendatahub` —
# see config/overlays/rhoai/kustomization.yaml. Override explicitly, e.g.
# NAMESPACE=redhat-ods-applications bash images/test-run.sh
export NAMESPACE
MLFLOW_NAME="mlflow"
# SA name is set by the operator's Helm chart; see internal/controller/constants.go
MLFLOW_SA_NAME="${MLFLOW_SA_NAME:-mlflow-sa}"
TRACE_ARCHIVAL_RETENTION="${TRACE_ARCHIVAL_RETENTION:-1m}"

MLFLOW_TAG="${MLFLOW_TAG:-master}"
MLFLOW_IMAGE_REPO="${MLFLOW_IMAGE_REPO:-}"
MLFLOW_IMAGE="${MLFLOW_IMAGE:-}"
MLFLOW_OPERATOR_IMAGE="${MLFLOW_OPERATOR_IMAGE:-quay.io/opendatahub/mlflow-operator:odh-stable}"

# Legacy DB_TYPE support: map to BACKEND_STORE/REGISTRY_STORE if they aren't set.
# Jenkins sets this instead of BACKEND_STORE/REGISTRY_STORE.
if [ -n "${DB_TYPE:-}" ]; then
    case "$DB_TYPE" in
        postgresql|postgres)
            BACKEND_STORE="${BACKEND_STORE:-postgres}"
            REGISTRY_STORE="${REGISTRY_STORE:-postgres}"
            ;;
        sqlite)
            BACKEND_STORE="${BACKEND_STORE:-sqlite}"
            REGISTRY_STORE="${REGISTRY_STORE:-sqlite}"
            ;;
        *)
            echo "ERROR: Unsupported DB_TYPE='${DB_TYPE}'. Use BACKEND_STORE and REGISTRY_STORE instead." >&2
            fail_run "test_config" "Unsupported DB_TYPE='${DB_TYPE}'. Use BACKEND_STORE and REGISTRY_STORE instead."
            ;;
    esac
fi
BACKEND_STORE="${BACKEND_STORE:-sqlite}"
REGISTRY_STORE="${REGISTRY_STORE:-sqlite}"

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
CLEANUP_REUSED_RESOURCES="${CLEANUP_REUSED_RESOURCES:-false}"
FAIL_FAST="${FAIL_FAST:-true}"
FORCE_PORT_FORWARD="${FORCE_PORT_FORWARD:-false}"
SERVE_ARTIFACTS="${SERVE_ARTIFACTS:-${serve_artifacts:-true}}"
ARTIFACTS_SERVER="${ARTIFACTS_SERVER:-false}"
ARTIFACTS_SERVER_GATEWAY="${ARTIFACTS_SERVER_GATEWAY:-false}"
OVERALL_EXIT=0

ARTIFACT_BACKENDS_CONFIGURED=false
STORAGE_TYPE_CONFIGURED=false
[ -n "${ARTIFACT_BACKENDS+x}" ] && ARTIFACT_BACKENDS_CONFIGURED=true
[ -n "${STORAGE_TYPE+x}" ] && STORAGE_TYPE_CONFIGURED=true

# Suites to run. Each entry is an artifact storage backend (file|s3|externals3); the script
# deploys a fresh MLflow CR per suite, runs the full test suite, then deletes that CR
# (including after the last suite, and after pytest failures).
# Backward compatibility: STORAGE_TYPE=<type> (old single-suite interface) is honoured
# when ARTIFACT_BACKENDS is not explicitly set.
if ! $ARTIFACT_BACKENDS_CONFIGURED; then
    if $STORAGE_TYPE_CONFIGURED; then
        ARTIFACT_BACKENDS="${STORAGE_TYPE}"
    else
        ARTIFACT_BACKENDS="file,s3"
    fi
fi
# STORAGE_TYPE is set per-iteration by the main loop; this default is only used if
# run_suite is somehow called outside the loop (e.g. during development/debugging).
STORAGE_TYPE="${STORAGE_TYPE:-file}"
UPGRADE_TEST_WORKSPACE="${upgrade_test_workspace:-${upgrade_workspace:-mlflow-upgrade-test-workspace}}"
export upgrade_test_workspace="$UPGRADE_TEST_WORKSPACE"
export upgrade_workspace="$UPGRADE_TEST_WORKSPACE"

if [ -n "$INFERRED_UPGRADE_PHASE" ]; then
    if ! $ARTIFACT_BACKENDS_CONFIGURED && ! $STORAGE_TYPE_CONFIGURED; then
        ARTIFACT_BACKENDS="file"
    fi

    mapfile -t _upgrade_backends < <(printf '%s\n' "$ARTIFACT_BACKENDS" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d')
    if [ "${#_upgrade_backends[@]}" -ne 1 ]; then
        echo "ERROR: Upgrade pytest phases require exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE." >&2
        fail_run "test_config" "Upgrade pytest phases require exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE."
    fi
    ARTIFACT_BACKENDS="${_upgrade_backends[0]}"
    STORAGE_TYPE="$ARTIFACT_BACKENDS"
fi

_compact_artifact_backends="$(printf '%s' "$ARTIFACT_BACKENDS" | tr -d '[:space:]')"
case "$_compact_artifact_backends" in
    ,*|*,|*,,*)
        echo "ERROR: ARTIFACT_BACKENDS must not contain empty entries." >&2
        fail_run "test_config" "ARTIFACT_BACKENDS must not contain empty entries."
        ;;
esac

mapfile -t _resolved_backends < <(printf '%s\n' "$ARTIFACT_BACKENDS" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d')
ARTIFACT_BACKEND_COUNT="${#_resolved_backends[@]}"

if [ "$ARTIFACT_BACKEND_COUNT" -eq 0 ]; then
    echo "ERROR: ARTIFACT_BACKENDS must contain at least one of: file, s3, externals3." >&2
    fail_run "test_config" "ARTIFACT_BACKENDS must contain at least one of: file, s3, externals3."
fi
for artifact_backend in "${_resolved_backends[@]}"; do
    case "$artifact_backend" in
        file|s3|externals3) ;;
        *)
            echo "ERROR: Unsupported ARTIFACT_BACKENDS value: '${artifact_backend}'. Supported: file, s3, externals3." >&2
            fail_run "test_config" "Unsupported ARTIFACT_BACKENDS value: '${artifact_backend}'. Supported: file, s3, externals3."
            ;;
    esac
    if [ "$artifact_backend" = "externals3" ] && \
       { [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ] || [ -z "${BUCKET:-}" ]; }; then
        echo "ERROR: externals3 requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and BUCKET." >&2
        fail_run "test_config" "externals3 requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and BUCKET."
    fi
done

if [ "$SKIP_CLEANUP" = "true" ] && [ "$ARTIFACT_BACKEND_COUNT" -ne 1 ]; then
    echo "ERROR: SKIP_CLEANUP=true requires exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE." >&2
    fail_run "test_config" "SKIP_CLEANUP=true requires exactly one backend via ARTIFACT_BACKENDS or STORAGE_TYPE."
fi
if [ "$SKIP_DEPLOYMENT" = "true" ] && [ "$ARTIFACT_BACKEND_COUNT" -ne 1 ]; then
    echo "ERROR: SKIP_DEPLOYMENT=true requires exactly one backend matching the reused MLflow deployment." >&2
    fail_run "test_config" "SKIP_DEPLOYMENT=true requires exactly one backend matching the reused MLflow deployment."
fi

case "$CLEANUP_REUSED_RESOURCES" in
    false|true|on_success) ;;
    *)
        echo "ERROR: CLEANUP_REUSED_RESOURCES must be one of: false, true, on_success." >&2
        fail_run "test_config" "CLEANUP_REUSED_RESOURCES must be one of: false, true, on_success."
        ;;
esac

# Platform for infrastructure overlays: base|openshift.
# Defaults to openshift only when the cluster actually exposes route resources;
# otherwise falls back to base. Can always be overridden explicitly.
if [ -z "${INFRASTRUCTURE_PLATFORM:-}" ]; then
    if kubectl api-resources --api-group=route.openshift.io -o name 2>/dev/null | grep -q .; then
        INFRASTRUCTURE_PLATFORM="openshift"
    else
        INFRASTRUCTURE_PLATFORM="base"
    fi
fi

if [ "$ARTIFACTS_SERVER" = "true" ]; then
    if [ "$ARTIFACTS_SERVER_GATEWAY" = "true" ] && [ "$INFRASTRUCTURE_PLATFORM" != "openshift" ]; then
        echo "ERROR: ARTIFACTS_SERVER_GATEWAY=true requires a Gateway-capable OpenShift cluster." >&2
        fail_run "test_config" "ARTIFACTS_SERVER_GATEWAY=true requires a Gateway-capable OpenShift cluster."
    fi
    if [ "$ARTIFACTS_SERVER_GATEWAY" = "true" ] && [ "$FORCE_PORT_FORWARD" = "true" ]; then
        echo "ERROR: ARTIFACTS_SERVER_GATEWAY=true cannot use FORCE_PORT_FORWARD; the test must traverse the Gateway." >&2
        fail_run "test_config" "ARTIFACTS_SERVER_GATEWAY=true cannot use FORCE_PORT_FORWARD; the test must traverse the Gateway."
    fi
    case "$BACKEND_STORE" in
        postgres|postgresql) ;;
        *)
            echo "ERROR: ARTIFACTS_SERVER=true requires BACKEND_STORE=postgres and REGISTRY_STORE=postgres." >&2
            fail_run "test_config" "ARTIFACTS_SERVER=true requires BACKEND_STORE=postgres and REGISTRY_STORE=postgres."
            ;;
    esac
    case "$REGISTRY_STORE" in
        postgres|postgresql) ;;
        *)
            echo "ERROR: ARTIFACTS_SERVER=true requires BACKEND_STORE=postgres and REGISTRY_STORE=postgres." >&2
            fail_run "test_config" "ARTIFACTS_SERVER=true requires BACKEND_STORE=postgres and REGISTRY_STORE=postgres."
            ;;
    esac
    SERVE_ARTIFACTS=false
elif [ "$ARTIFACTS_SERVER_GATEWAY" = "true" ]; then
    echo "ERROR: ARTIFACTS_SERVER_GATEWAY=true requires ARTIFACTS_SERVER=true." >&2
    fail_run "test_config" "ARTIFACTS_SERVER_GATEWAY=true requires ARTIFACTS_SERVER=true."
fi

# Infrastructure image overrides
POSTGRES_IMAGE="${POSTGRES_IMAGE:-}"
SEAWEEDFS_IMAGE="${SEAWEEDFS_IMAGE:-}"

# TLS flags for self-deployed infrastructure
POSTGRES_TLS="${POSTGRES_TLS:-false}"
SEAWEEDFS_TLS="${SEAWEEDFS_TLS:-false}"
CA_BUNDLE_PATH="${CA_BUNDLE_PATH:-}"
CA_BUNDLE_CONFIGMAP="${CA_BUNDLE_CONFIGMAP:-}"

# S3 endpoint URL: prefer explicit S3_ENDPOINT_URL, fall back to AWS_DEFAULT_ENDPOINT
# (Jenkins on disconnected clusters sets AWS_DEFAULT_ENDPOINT to the bastion MinIO URL).
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-${AWS_DEFAULT_ENDPOINT:-}}"

# PostgreSQL sslmode appended to the connection URI.
# Leave empty to let deploy.py use its default ("disable" for self-deployed postgres).
DB_SSLMODE="${DB_SSLMODE:-}"

# LC_ALL=C is required: macOS tr treats /dev/urandom as UTF-8 and exits with
# "Illegal byte sequence" under a UTF-8 locale (set -e then aborts the script).
# Read a finite blob first so tr sees EOF instead of SIGPIPE; with pipefail,
# `tr </dev/urandom | head` exits 141 on Linux and aborts the suite.
RANDOM_SUFFIX=$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 8)
WORKSPACE_LIST="${workspaces:-workspace1-${RANDOM_SUFFIX},workspace2-${RANDOM_SUFFIX}}"
if [ -n "$INFERRED_UPGRADE_PHASE" ]; then
    WORKSPACE_LIST="$UPGRADE_TEST_WORKSPACE"
fi
# Export so pytest (Config.WORKSPACES) reads the same names RBAC is set up for
export workspaces="$WORKSPACE_LIST"

PF_PID=""
S3_PF_PID=""
ARTIFACTS_PF_PID=""
TEST_CA_BUNDLE_FILE=""
declare -A _TEST_CA_ENV_VALUES=()
declare -A _TEST_CA_ENV_WAS_SET=()
_TEST_CA_ENV_CAPTURED=false
_CREATED_WORKSPACES=""  # tracks only namespaces created by this run (not pre-existing)
# Set to true after the first suite so subsequent suites skip re-deploying the operator.
_OPERATOR_DEPLOYED=false

MLFLOW_DEFAULT_IMAGE=""
if [ -n "${MLFLOW_IMAGE_REPO:-}" ] && [ -n "${MLFLOW_TAG:-}" ]; then
    MLFLOW_DEFAULT_IMAGE="${MLFLOW_IMAGE_REPO}:${MLFLOW_TAG}"
fi
MLFLOW_RESOLVED_IMAGE="${MLFLOW_IMAGE:-${MLFLOW_DEFAULT_IMAGE}}"

should_use_mlflow_static_prefix() {
    if [ "$INFERRED_UPGRADE_PHASE" != "pre_upgrade" ] && [ "$INFERRED_UPGRADE_PHASE" != "post_upgrade" ]; then
        return 0
    fi

    local version="${MLFLOW_TEST_SUPPORTED_VERSION:-}"
    if [ -z "$version" ]; then
        return 0
    fi

    local major="${version%%.*}"
    local remainder="${version#*.}"
    local minor="${remainder%%.*}"

    if ! [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]]; then
        return 0
    fi

    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        return 1
    fi

    return 0
}

should_use_mlflow_prefixed_health_endpoint() {
    return 0
}

stop_port_forwards() {
    local pid
    for pid in "$PF_PID" "$S3_PF_PID" "$ARTIFACTS_PF_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    PF_PID=""
    S3_PF_PID=""
    ARTIFACTS_PF_PID=""
}

cleanup_self_managed_infrastructure() {
    local cleanup_internal_s3="${1:-false}"
    local wait_for_delete="${2:-false}"

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
    kustomize build "$TEST_INFRA_ROOT/postgres/$postgres_overlay" \
        | kubectl delete --ignore-not-found -n "$NAMESPACE" -f - 2>/dev/null || true

    if [ "$wait_for_delete" = "true" ]; then
        kubectl wait --for=delete deployment/postgres-deployment --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
        kubectl wait --for=delete pod -l app=mlflow-postgres --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
        kubectl wait --for=delete pvc/postgres-pvc --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
    fi

    # Only tear down SeaweedFS if the in-cluster s3 backend was used.
    # externals3 uses an external S3 service that this run did not deploy.
    if [ "$cleanup_internal_s3" = "true" ]; then
        export APPLICATION_CRD_ID=mlflow-pipelines \
               PROFILE_NAMESPACE_LABEL=mlflow-profile \
               S3_BUCKET="${BUCKET:-mlpipeline}"
        kustomize build "$TEST_INFRA_ROOT/seaweedfs/$seaweedfs_overlay" \
            | envsubst '$NAMESPACE,$APPLICATION_CRD_ID,$PROFILE_NAMESPACE_LABEL,$S3_BUCKET' \
            | kubectl delete --ignore-not-found -f - 2>/dev/null || true

        if [ "$wait_for_delete" = "true" ]; then
            kubectl wait --for=delete deployment/seaweedfs --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
            kubectl wait --for=delete job/init-seaweedfs --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
            kubectl wait --for=delete pod -l app=seaweedfs --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
            kubectl wait --for=delete pvc/seaweedfs-pvc --namespace "$NAMESPACE" --timeout=180s 2>/dev/null || true
        fi
    fi

    # Clean up TLS resources (cert Secrets, CA bundle ConfigMap, DSCI restore)
    # after infrastructure is torn down to avoid noisy pod errors from missing secrets.
    if [ "${POSTGRES_TLS:-false}" = "true" ] || [ "${SEAWEEDFS_TLS:-false}" = "true" ]; then
        local _tls_args=(--namespace "$NAMESPACE")
        [ "${POSTGRES_TLS:-false}"  = "true" ] && _tls_args+=(--postgres-tls)
        [ "${SEAWEEDFS_TLS:-false}" = "true" ] && _tls_args+=(--seaweedfs-tls)
        uv run python3 "$DEPLOY_PY" --cleanup-tls "${_tls_args[@]}" 2>/dev/null || true
    fi
}

collect_debug_logs() {
    local failure_reason="${1:-failure}"

    echo "  Collecting debug logs after ${failure_reason}..."
    if ! "$SCRIPT_DIR/collect-debug-logs.sh" \
        --namespace "$NAMESPACE" \
        --output-dir "${TEST_RESULTS_DIR}/debug"; then
        echo "WARN: debug log collection failed for namespace '${NAMESPACE}' after ${failure_reason}" >&2
    fi
}

restore_test_ca_bundle_environment() {
    [ -n "$TEST_CA_BUNDLE_FILE" ] && rm -f "$TEST_CA_BUNDLE_FILE"
    TEST_CA_BUNDLE_FILE=""

    if [ "$_TEST_CA_ENV_CAPTURED" != "true" ]; then
        return
    fi

    local name
    for name in ca_bundle SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE AWS_CA_BUNDLE; do
        if [ "${_TEST_CA_ENV_WAS_SET[$name]:-false}" = "true" ]; then
            printf -v "$name" '%s' "${_TEST_CA_ENV_VALUES[$name]}"
            export "$name"
        else
            unset "$name"
        fi
    done
    _TEST_CA_ENV_VALUES=()
    _TEST_CA_ENV_WAS_SET=()
    _TEST_CA_ENV_CAPTURED=false
}

configure_test_ca_bundle() {
    restore_test_ca_bundle_environment
    if [ "$STORAGE_TYPE" != "s3" ] || [ "$SEAWEEDFS_TLS" != "true" ]; then
        return 0
    fi

    local name
    for name in ca_bundle SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE AWS_CA_BUNDLE; do
        if [[ -v "$name" ]]; then
            _TEST_CA_ENV_VALUES[$name]="${!name}"
            _TEST_CA_ENV_WAS_SET[$name]=true
        fi
    done
    _TEST_CA_ENV_CAPTURED=true

    local configmap_name="${CA_BUNDLE_CONFIGMAP:-mlflow-ca-bundle}"
    local custom_ca_file
    custom_ca_file="$(mktemp)"
    TEST_CA_BUNDLE_FILE="$(mktemp)"

    if ! kubectl get configmap "$configmap_name" --namespace "$NAMESPACE" -o json \
        | uv run --project "$UV_PROJECT_DIR" --no-sync python -c '
import json
import sys

data = json.load(sys.stdin).get("data", {})
certificates = [data[key] for key in sorted(data) if key.endswith((".crt", ".pem"))]
if not certificates:
    raise SystemExit("ConfigMap has no .crt or .pem entries")
sys.stdout.write("\n".join(certificates))
' > "$custom_ca_file"; then
        echo "ERROR: Failed to read .crt or .pem certificates from ConfigMap ${configmap_name}" >&2
        rm -f "$custom_ca_file"
        restore_test_ca_bundle_environment
        return 1
    fi
    if ! grep -q "BEGIN CERTIFICATE" "$custom_ca_file"; then
        echo "ERROR: ConfigMap ${configmap_name} does not contain a valid CA certificate" >&2
        rm -f "$custom_ca_file"
        restore_test_ca_bundle_environment
        return 1
    fi

    local system_ca_bundle="/etc/pki/tls/certs/ca-bundle.crt"
    if [ -f "$system_ca_bundle" ]; then
        cat "$system_ca_bundle" "$custom_ca_file" > "$TEST_CA_BUNDLE_FILE"
    else
        mv "$custom_ca_file" "$TEST_CA_BUNDLE_FILE"
        custom_ca_file=""
    fi
    [ -n "$custom_ca_file" ] && rm -f "$custom_ca_file"

    export ca_bundle="$TEST_CA_BUNDLE_FILE"
    export SSL_CERT_FILE="$TEST_CA_BUNDLE_FILE"
    export REQUESTS_CA_BUNDLE="$TEST_CA_BUNDLE_FILE"
    export CURL_CA_BUNDLE="$TEST_CA_BUNDLE_FILE"
    export AWS_CA_BUNDLE="$TEST_CA_BUNDLE_FILE"
    echo "  Configured test clients to trust ${configmap_name}"
}

wait_for_mlflow_cr_available() {
    echo "  Waiting for MLflow CR to report Available=True..."
    if kubectl wait \
        --for=condition=Available \
        "mlflow/${MLFLOW_NAME}" \
        --namespace "$NAMESPACE" \
        --timeout=300s; then
        local available_reason=""
        available_reason="$(kubectl get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" -o jsonpath="{.status.conditions[?(@.type=='Available')].reason}" 2>/dev/null || true)"
        echo "  MLflow CR reports Available=True${available_reason:+ (reason: ${available_reason})}"
        return 0
    fi

    local available_status=""
    local available_reason=""
    available_status="$(kubectl get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" -o jsonpath="{.status.conditions[?(@.type=='Available')].status}" 2>/dev/null || true)"
    available_reason="$(kubectl get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" -o jsonpath="{.status.conditions[?(@.type=='Available')].reason}" 2>/dev/null || true)"
    echo "ERROR: MLflow CR did not report Available=True within timeout (last status: ${available_status:-missing}, reason: ${available_reason:-missing})" >&2
    collect_debug_logs "status availability failure"
    fail_suite "test_wait_for_mlflow_cr_available" \
        "MLflow CR did not report Available=True within timeout (last status: ${available_status:-missing}, reason: ${available_reason:-missing})"
    return 1
}

wait_for_mlflow_server_info() {
    local api_url="${MLFLOW_TRACKING_URI%/}/api/3.0/mlflow/server-info"
    local retry=0
    local max_retries=36  # 36 × 5 s = 3 min
    local debug_dir="${TEST_RESULTS_DIR}/debug"
    local body_file="${debug_dir}/server-info-last-body.txt"
    local err_file="${debug_dir}/server-info-last-stderr.txt"
    local http_code=""
    local err_preview body_preview
    mkdir -p "$debug_dir"

    echo "  Waiting for MLflow server-info endpoint at $api_url..."
    while true; do
        http_code="$(curl -skS --connect-timeout 5 --max-time 5 \
            -H "Authorization: Bearer ${kube_token}" \
            -o "$body_file" -w "%{http_code}" "$api_url" 2>"$err_file" || true)"
        [ -n "$http_code" ] || http_code="000"
        if [ "$http_code" = "200" ]; then
            echo "  MLflow server-info endpoint is reachable (HTTP 200)"
            return 0
        fi
        retry=$((retry + 1))
        err_preview="$(tr '\n' ' ' < "$err_file" 2>/dev/null | head -c 180 || true)"
        body_preview="$(tr '\n' ' ' < "$body_file" 2>/dev/null | head -c 180 || true)"
        echo "  Attempt $retry/$max_retries HTTP=${http_code} err=${err_preview:-none} body=${body_preview:-<empty>}"
        if [ "$retry" -ge "$max_retries" ]; then
            echo "ERROR: MLflow server-info endpoint did not become reachable within timeout" >&2
            collect_debug_logs "server-info readiness failure"
            fail_suite "test_wait_for_mlflow_server_info" \
                "MLflow server-info endpoint did not become reachable within timeout (url: ${api_url}, last_http=${http_code})"
            return 1
        fi
        sleep 5
    done
}

wait_for_artifacts_server_route() {
    echo "  Waiting for the dedicated artifact Deployment..."
    if ! kubectl wait --for=condition=Available deployment/mlflow-artifacts \
        --namespace "$NAMESPACE" --timeout=300s; then
        echo "ERROR: mlflow-artifacts Deployment did not become available" >&2
        collect_debug_logs "artifact deployment readiness failure"
        fail_suite "test_wait_for_artifacts_server_route" \
            "mlflow-artifacts Deployment did not become available"
        return 1
    fi

    if [ "$ARTIFACTS_SERVER_GATEWAY" != "true" ]; then
        return 0
    fi

    echo "  Waiting for the dedicated artifact Gateway route..."

    local retry=0
    local max_retries=60
    local route_conditions=""
    until route_conditions=$(kubectl get httproute mlflow-artifacts -n "$NAMESPACE" \
        -o jsonpath='{range .status.parents[*].conditions[*]}{.type}={.status}{"\n"}{end}' 2>/dev/null) && \
        grep -qx "Accepted=True" <<<"$route_conditions" && \
        grep -qx "ResolvedRefs=True" <<<"$route_conditions"; do
        retry=$((retry + 1))
        if [ "$retry" -ge "$max_retries" ]; then
            echo "ERROR: mlflow-artifacts HTTPRoute was not accepted within timeout" >&2
            collect_debug_logs "artifact route acceptance failure"
            fail_suite "test_wait_for_artifacts_server_route" \
                "mlflow-artifacts HTTPRoute was not accepted within timeout"
            return 1
        fi
        sleep 5
    done

    local artifacts_url=""
    retry=0
    max_retries=12
    until artifacts_url=$(kubectl get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" -o jsonpath='{.status.artifactsUrl}' 2>/dev/null) && \
        [ -n "$artifacts_url" ]; do
        retry=$((retry + 1))
        if [ "$retry" -ge "$max_retries" ]; then
            echo "ERROR: MLflow CR status.artifactsUrl is empty with ARTIFACTS_SERVER=true" >&2
            collect_debug_logs "artifact route URL failure"
            fail_suite "test_wait_for_artifacts_server_route" \
                "MLflow CR status.artifactsUrl is empty with ARTIFACTS_SERVER=true"
            return 1
        fi
        sleep 5
    done
    echo "  Artifact route accepted; status.artifactsUrl=$artifacts_url"
}

should_cleanup_reused_resources() {
    local cleanup_status="${1:-${OVERALL_EXIT:-0}}"
    case "$CLEANUP_REUSED_RESOURCES" in
        true)
            return 0
            ;;
        on_success)
            [ "$cleanup_status" -eq 0 ]
            ;;
        *)
            return 1
            ;;
    esac
}

should_delete_mlflow_instance() {
    local cleanup_status="${1:-${OVERALL_EXIT:-0}}"
    [ "$SKIP_CLEANUP" != "true" ] || return 1
    if [ "$SKIP_DEPLOYMENT" != "true" ]; then
        return 0
    fi
    should_cleanup_reused_resources "$cleanup_status"
}

# The MLflow CR is cluster-scoped. A leftover instance keeps the
# mlflow.opendatahub.io/mlflow-operator-protection finalizer from allowing
# MLflowOperator to reach Removed. Wait until the object is gone so a later
# platform sweep does not time out on MLflowInstancesPresent.
delete_mlflow_instance() {
    "$_MLFLOW_INSTANCE_DELETED" && return 0
    echo "  Deleting cluster-scoped MLflow CR ${MLFLOW_NAME}..."
    if ! kubectl delete mlflow "$MLFLOW_NAME" --ignore-not-found --wait --timeout=120s; then
        echo "ERROR: failed to delete MLflow CR ${MLFLOW_NAME}; leftover instances block MLflowOperator removal" >&2
        kubectl get mlflow "$MLFLOW_NAME" -o yaml >&2 || true
        return 1
    fi
    _MLFLOW_INSTANCE_DELETED=true
}

# ─── Shared teardown (EXIT / INT / TERM trap) ─────────────────────────────────
# Removes all resources created by this run: workspace namespaces (only those the
# script itself created, not pre-existing ones), role bindings, the MLflow CR,
# and any self-deployed infrastructure (PostgreSQL, SeaweedFS).
# The DataScienceCluster mlflowoperator component is assumed to remain Managed.
# INT/TERM are trapped because Jenkins and Kubernetes send SIGTERM when a stage
# times out; EXIT alone does not run in that case, and SIGKILL follows later.

_CLEANUP_DONE=false
_MLFLOW_INSTANCE_DELETED=false
cleanup() {
    local cleanup_status="${1:-${OVERALL_EXIT:-0}}"
    "$_CLEANUP_DONE" && return
    _CLEANUP_DONE=true
    restore_test_ca_bundle_environment
    if [ "$SKIP_CLEANUP" = "true" ]; then
        return
    fi

    stop_port_forwards

    local should_cleanup_mlflow=false
    local should_cleanup_infrastructure=false
    local cleanup_internal_s3=false
    if should_delete_mlflow_instance "$cleanup_status"; then
        should_cleanup_mlflow=true
    fi
    if [ "$SKIP_INFRASTRUCTURE" != "true" ]; then
        if [ "$SKIP_DEPLOYMENT" != "true" ] || should_cleanup_reused_resources "$cleanup_status"; then
            should_cleanup_infrastructure=true
        fi
    fi

    # Only delete namespaces this run created; pre-existing namespaces are left intact.
    for ws in $(echo "$_CREATED_WORKSPACES" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
        kubectl delete namespace "$ws" --ignore-not-found 2>/dev/null || true
    done

    if [ "$should_cleanup_mlflow" = "true" ]; then
        for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
            ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
            kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" -n "$ws" --ignore-not-found 2>/dev/null || true
        done
        delete_mlflow_instance || true
        kubectl delete rolebinding "mlflow-permissions-${MLFLOW_NAME}" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrolebinding "mlflow-auth-delegator-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrolebinding "mlflow-config-view-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true
        kubectl delete clusterrole "mlflow-config-reader-${MLFLOW_NAME}" --ignore-not-found 2>/dev/null || true
    fi

    if [ "$should_cleanup_infrastructure" = "true" ]; then
        if echo "$ARTIFACT_BACKENDS" | tr ',' '\n' | grep -qxF 's3'; then
            cleanup_internal_s3=true
        fi
        cleanup_self_managed_infrastructure "$cleanup_internal_s3"
    fi
}

terminate_on_signal() {
    local signal_status="$1"
    trap - EXIT INT TERM
    cleanup "$signal_status"
    exit "$signal_status"
}

trap 'cleanup "$?"' EXIT
trap 'terminate_on_signal 130' INT
trap 'terminate_on_signal 143' TERM

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
        fail_run "test_patch_csv" "Failed to patch OLM CSV with MLflow operator manifests"
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

finalize_suite() {
    local suite_status="$1"
    stop_port_forwards

    if should_delete_mlflow_instance "$suite_status"; then
        echo "  Removing MLflow instance ${MLFLOW_NAME} after the ${STORAGE_TYPE} suite..."
        delete_mlflow_instance || OVERALL_EXIT=1
    fi

    if [ "$SUITE_HAS_NEXT" = "true" ] && \
       { [ "$SKIP_DEPLOYMENT" != "true" ] || should_cleanup_reused_resources "$suite_status"; } && \
       [ "$SKIP_INFRASTRUCTURE" != "true" ]; then
        echo "  Resetting suite infrastructure before the next backend..."
        local cleanup_internal_s3=false
        if [ "$STORAGE_TYPE" = "s3" ]; then
            cleanup_internal_s3=true
        fi
        cleanup_self_managed_infrastructure "$cleanup_internal_s3" "true"
    fi
}

run_suite_body() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Suite: storage=${STORAGE_TYPE} backend=${BACKEND_STORE} registry=${REGISTRY_STORE}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # ── Workspace namespaces (idempotent) ────────────────────────────────────────
    for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
        ws=$(echo "$ws" | xargs); [ -z "$ws" ] && continue
        if ! kubectl get namespace "$ws" &>/dev/null; then
            if ! kubectl create namespace "$ws"; then
                fail_suite "test_create_workspace_namespace" "Failed to create workspace namespace ${ws}"
                return 1
            fi
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
            --serve-artifacts       "$SERVE_ARTIFACTS"
        )
        [ "$ARTIFACTS_SERVER" = "true" ] && deploy_args+=(--artifacts-server)
        if [ "$ARTIFACTS_SERVER" = "true" ] && \
           [ "$ARTIFACTS_SERVER_GATEWAY" != "true" ] && \
           [ "$INFRASTRUCTURE_PLATFORM" != "openshift" ]; then
            deploy_args+=(--mlflow-url "https://localhost:8444")
        fi
        [ -n "${MLFLOW_RESOLVED_IMAGE}" ] && deploy_args+=(--mlflow-image "$MLFLOW_RESOLVED_IMAGE")

        [ -n "${POSTGRES_IMAGE:-}"  ] && deploy_args+=(--postgres-image  "$POSTGRES_IMAGE")
        [ -n "${SEAWEEDFS_IMAGE:-}" ] && deploy_args+=(--seaweedfs-image "$SEAWEEDFS_IMAGE")
        [ -n "${DB_SSLMODE:-}"      ] && deploy_args+=(--postgres-sslmode "$DB_SSLMODE")
        [ -n "${TRACE_ARCHIVAL_RETENTION:-}" ] && deploy_args+=(--trace-archival-retention "$TRACE_ARCHIVAL_RETENTION")
        [ "${POSTGRES_TLS:-false}"  = "true" ] && deploy_args+=(--postgres-tls)
        [ "${SEAWEEDFS_TLS:-false}" = "true" ] && deploy_args+=(--seaweedfs-tls)
        [ -n "${CA_BUNDLE_PATH:-}"      ] && deploy_args+=(--ca-bundle-path       "$CA_BUNDLE_PATH")
        [ -n "${CA_BUNDLE_CONFIGMAP:-}" ] && deploy_args+=(--ca-bundle-configmap  "$CA_BUNDLE_CONFIGMAP")
        [ -n "${WORKSPACE_LABEL_SELECTOR:-}" ] && deploy_args+=(--workspace-label-selector "$WORKSPACE_LABEL_SELECTOR")

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
                # Optional: S3_ENDPOINT_URL or AWS_DEFAULT_ENDPOINT, AWS_DEFAULT_REGION.
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
                fail_suite "test_artifact_backend" \
                    "Unsupported ARTIFACT_BACKENDS value: '${STORAGE_TYPE}'. Supported: file, s3, externals3"
                return 1
                ;;
        esac

        case "$BACKEND_STORE" in
            postgresql|postgres)
                deploy_args+=(--backend-store postgres)
                ;;
            *)
                deploy_args+=(--backend-store sqlite)
                ;;
        esac

        case "$REGISTRY_STORE" in
            postgresql|postgres)
                deploy_args+=(--registry-store postgres)
                ;;
            *)
                deploy_args+=(--registry-store sqlite)
                ;;
        esac

        if [ "$BACKEND_STORE" = "postgres" ] || [ "$BACKEND_STORE" = "postgresql" ] || \
           [ "$REGISTRY_STORE" = "postgres" ] || [ "$REGISTRY_STORE" = "postgresql" ]; then
            [ -n "${DB_HOST:-}"     ] && deploy_args+=(--postgres-host        "$DB_HOST")
            [ -n "${DB_PORT:-}"     ] && deploy_args+=(--postgres-port        "$DB_PORT")
            [ -n "${DB_USER:-}"     ] && deploy_args+=(--postgres-user        "$DB_USER")
            [ -n "${DB_PASSWORD:-}" ] && deploy_args+=(--postgres-password    "$DB_PASSWORD")
        fi
        if [ "$BACKEND_STORE" = "postgres" ] || [ "$BACKEND_STORE" = "postgresql" ]; then
            [ -n "${DB_NAME:-}" ] && deploy_args+=(--postgres-backend-db "$DB_NAME")
        fi
        if [ "$REGISTRY_STORE" = "postgres" ] || [ "$REGISTRY_STORE" = "postgresql" ]; then
            [ -n "${DB_NAME:-}" ] && deploy_args+=(--postgres-registry-db "$DB_NAME")
        fi

        local deploy_rc=0
        uv run --project "$UV_PROJECT_DIR" --no-sync "$DEPLOY_PY" "${deploy_args[@]}" || deploy_rc=$?
        if [ "$deploy_rc" -ne 0 ]; then
            collect_debug_logs "deploy.py failure"
            fail_suite "test_deploy" "deploy.py failed (exit code ${deploy_rc})"
            return 1
        fi
        _OPERATOR_DEPLOYED=true
    fi

    # ── RBAC ────────────────────────────────────────────────────────────────────
    # Applied after deploy.py so the SA exists; runs before tests execute.
    if ! setup_rbac; then
        fail_suite "test_setup_rbac" "Failed to set up RBAC for ${MLFLOW_SA_NAME}"
        return 1
    fi

    # ── Tracking URI ────────────────────────────────────────────────────────────
    if [ "$INFRASTRUCTURE_PLATFORM" = "openshift" ] && [ "$FORCE_PORT_FORWARD" != "true" ]; then
        echo "  Waiting for MLflow CR status.url to be populated..."
        local external_url=""
        local addr_retry=0
        local addr_max=60  # 60 × 5 s = 5 min
        while [ -z "$external_url" ]; do
            external_url=$(kubectl get mlflow "$MLFLOW_NAME" -o jsonpath='{.status.url}' 2>/dev/null || true)
            [ -n "$external_url" ] && break
            addr_retry=$((addr_retry + 1))
            if [ "$addr_retry" -ge "$addr_max" ]; then
                echo "ERROR: MLflow CR status.url not populated within timeout" >&2
                collect_debug_logs "status.url readiness failure"
                fail_suite "test_wait_for_mlflow_status_url" \
                    "MLflow CR status.url not populated within timeout"
                return 1
            fi
            echo "  Attempt $addr_retry/$addr_max — retrying in 5s..."
            sleep 5
        done
        local tracking_url="$external_url"
        if ! should_use_mlflow_static_prefix; then
            tracking_url="${external_url%/mlflow}"
            echo "  Using legacy tracking URI shape without /mlflow static prefix for upgrade MLflow ${MLFLOW_TEST_SUPPORTED_VERSION:-unknown}"
        fi
        export MLFLOW_TRACKING_URI="$tracking_url"
    else
        local mlflow_base_path="/mlflow"
        if ! should_use_mlflow_static_prefix; then
            mlflow_base_path=""
            echo "  Using legacy tracking URI shape without /mlflow static prefix for upgrade MLflow ${MLFLOW_TEST_SUPPORTED_VERSION:-unknown}"
        fi
        if [ "$INFRASTRUCTURE_PLATFORM" = "openshift" ] && [ "$FORCE_PORT_FORWARD" = "true" ]; then
            echo "  FORCE_PORT_FORWARD=true, using localhost port-forward instead of MLflow CR status.url"
        fi
        echo "  Port-forwarding MLflow service to localhost:8443..."
        kubectl port-forward "svc/${MLFLOW_NAME}" -n "$NAMESPACE" 8443:8443 &
        PF_PID=$!
        sleep 2
        export MLFLOW_TRACKING_URI="https://localhost:8443${mlflow_base_path}"
    fi
    echo "  MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI"

    # The external Gateway URL redirects unauthenticated requests to OAuth. Create
    # the service-account token before probing it so readiness exercises the same
    # authenticated API path as the tests below.
    echo "  Generating token for ${MLFLOW_SA_NAME}..."
    if ! kube_token=$(kubectl create token "$MLFLOW_SA_NAME" --namespace "$NAMESPACE"); then
        echo "ERROR: Failed to create token for $MLFLOW_SA_NAME" >&2
        fail_suite "test_create_kube_token" "Failed to create token for ${MLFLOW_SA_NAME}"
        return 1
    fi
    export kube_token

    # ── MLflow CR availability ─────────────────────────────────────────────────
    if ! wait_for_mlflow_cr_available; then
        return 1
    fi
    if ! wait_for_mlflow_server_info; then
        return 1
    fi
    if [ "$ARTIFACTS_SERVER" = "true" ] && ! wait_for_artifacts_server_route; then
        return 1
    fi
    if [ "$ARTIFACTS_SERVER" = "true" ]; then
        if [ "$ARTIFACTS_SERVER_GATEWAY" = "true" ]; then
            local published_artifacts_url
            published_artifacts_url="$(kubectl get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" -o jsonpath='{.status.artifactsUrl}')"
            export MLFLOW_ARTIFACTS_URI="${published_artifacts_url%/api/2.0/mlflow-artifacts/artifacts}"
        else
            echo "  Port-forwarding dedicated artifact service to localhost:8444..."
            kubectl port-forward "svc/mlflow-artifacts" -n "$NAMESPACE" 8444:8443 &
            ARTIFACTS_PF_PID=$!
            sleep 2
            export MLFLOW_ARTIFACTS_URI="https://localhost:8444/mlflow-artifacts"
        fi
        echo "  MLFLOW_ARTIFACTS_URI=$MLFLOW_ARTIFACTS_URI"
    fi

    if [ "$INFERRED_UPGRADE_PHASE" = "post_upgrade" ]; then
        echo "  Waiting for MLflow CR status.version to reach ${SUPPORTED_MLFLOW_VERSION_RAW}..."
        if ! kubectl wait \
            --for="jsonpath={.status.version}=${SUPPORTED_MLFLOW_VERSION_RAW}" \
            "mlflow/${MLFLOW_NAME}" \
            --namespace "$NAMESPACE" \
            --timeout=300s; then
            echo "ERROR: MLflow CR did not report status.version=${SUPPORTED_MLFLOW_VERSION_RAW} within timeout" >&2
            collect_debug_logs "status.version readiness failure"
            fail_suite "test_wait_for_mlflow_status_version" \
                "MLflow CR did not report status.version=${SUPPORTED_MLFLOW_VERSION_RAW} within timeout"
            return 1
        fi
        echo "  MLflow CR status.version matches ${SUPPORTED_MLFLOW_VERSION_RAW}"
    fi

    if [ "$STORAGE_TYPE" = "s3" ] && [ "$SKIP_INFRASTRUCTURE" != "true" ]; then
        echo "  Port-forwarding SeaweedFS S3 endpoint to localhost:9000..."
        kubectl port-forward service/minio-service 9000:9000 -n "$NAMESPACE" &
        S3_PF_PID=$!
        sleep 2
        # Some smoke paths, such as trace archival verification, talk to the
        # S3-compatible backend directly even when MLflow proxies artifacts.
        local s3_endpoint_scheme="http"
        if [ "${SEAWEEDFS_TLS:-false}" = "true" ]; then
            s3_endpoint_scheme="https"
        fi
        export MLFLOW_S3_ENDPOINT_URL="${MLFLOW_S3_ENDPOINT_URL:-${s3_endpoint_scheme}://localhost:9000}"
    elif [ "$STORAGE_TYPE" = "externals3" ] && [ -n "${S3_ENDPOINT_URL:-}" ]; then
        # Direct S3 clients read MLFLOW_S3_ENDPOINT_URL. The archival Job already
        # receives S3_ENDPOINT_URL/AWS_DEFAULT_ENDPOINT via deploy.py; export the
        # same resolved endpoint so object verification does not fall back to AWS.
        export MLFLOW_S3_ENDPOINT_URL="${MLFLOW_S3_ENDPOINT_URL:-${S3_ENDPOINT_URL}}"
    fi
    if ! configure_test_ca_bundle; then
        fail_suite "test_configure_ca_bundle" \
            "Failed to configure test clients with the SeaweedFS CA bundle"
        return 1
    fi

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
    export serve_artifacts="${SERVE_ARTIFACTS}"
    export TRACE_ARCHIVAL_RETENTION
    export artifacts_server="${ARTIFACTS_SERVER}"
    export artifacts_server_gateway="${ARTIFACTS_SERVER_GATEWAY}"
    export mlflow_namespace="${NAMESPACE}"
    export AWS_S3_BUCKET="${AWS_S3_BUCKET:-${BUCKET:-}}"
    local deployed_trace_archival
    if ! deployed_trace_archival="$(kubectl get mlflow "$MLFLOW_NAME" -o jsonpath='{.spec.traceArchival.enabled}')"; then
        echo "ERROR: Failed to read trace archival state from MLflow CR ${MLFLOW_NAME}" >&2
        fail_suite "test_read_trace_archival_state" "Failed to read trace archival state from MLflow CR ${MLFLOW_NAME}"
        restore_test_ca_bundle_environment
        return 1
    fi
    export trace_archival_enabled="${deployed_trace_archival:-false}"

    local results_file="${TEST_RESULTS_DIR}/xunit_report_${STORAGE_TYPE}.xml"
    echo "  Running tests (output: $results_file)..."
    cd "$SCRIPT_DIR/.."
    local suite_exit=0
    uv run --project "$UV_PROJECT_DIR" --no-sync pytest --junit-xml="$results_file" "${PYTEST_ARGS[@]}" || suite_exit=$?
    cd "$SCRIPT_DIR"

    if [ "$suite_exit" -ne 0 ]; then
        if ! "$SCRIPT_DIR/collect-debug-logs.sh" \
            --namespace "$NAMESPACE" \
            --output-dir "${TEST_RESULTS_DIR}/debug"; then
            echo "WARN: debug log collection failed for namespace '${NAMESPACE}'" >&2
        fi
    fi

    restore_test_ca_bundle_environment
    return "$suite_exit"
}

run_suite() {
    local suite_status=0
    _MLFLOW_INSTANCE_DELETED=false
    run_suite_body || suite_status=$?
    finalize_suite "$suite_status"
    return "$suite_status"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

for suite_idx in "${!_resolved_backends[@]}"; do
    STORAGE_TYPE="${_resolved_backends[$suite_idx]}"
    [ -z "$STORAGE_TYPE" ] && continue
    SUITE_HAS_NEXT=false
    if [ "$suite_idx" -lt $((ARTIFACT_BACKEND_COUNT - 1)) ]; then
        SUITE_HAS_NEXT=true
    fi
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
