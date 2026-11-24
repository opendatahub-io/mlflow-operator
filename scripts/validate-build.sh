#!/usr/bin/env bash
# Konflux PR build simulator for mlflow-operator.
#
# All paths and image definitions come from .tekton/*pull-request.yaml through
# validate_tekton_contracts.py. Nothing in this script guesses which Dockerfile
# or context Konflux uses.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT/scripts/validate_tekton_contracts.py"

cd "$ROOT"

die() {
  echo "❌ $*" >&2
  exit 1
}

ok() {
  echo "✅ $*"
}

info() {
  echo "   $*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

assert_nonempty_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ -z "$expected" ]; then
    die "$label expected value is empty"
  fi
  if [ -z "$actual" ]; then
    die "$label actual value is absent/empty"
  fi
  if [ "$expected" != "$actual" ]; then
    die "$label mismatch: expected '$expected', got '$actual'"
  fi
  ok "$label=$actual"
}

load_target() {
  local role="$1" assignment key value output
  # Resolve the complete target object from Tekton. Workflow-level env is only
  # a cross-job projection, so clear inherited values and accept only fields
  # emitted by a resolver call that actually succeeded.
  unset SIM_TARGET_IMAGE SIM_TARGET_DOCKERFILE SIM_TARGET_CONTEXT \
    SIM_TARGET_HERMETIC SIM_TARGET_PLATFORMS SIM_TARGET_PIPELINE_RUN
  if ! output="$(python3 "$HELPER" target-env "$role")"; then
    die "failed to resolve the Konflux target for role '$role'"
  fi
  while IFS= read -r assignment; do
    case "$assignment" in
      '')
        ;;
      SIM_TARGET_*=*)
        key="${assignment%%=*}"
        value="${assignment#*=}"
        printf -v "$key" '%s' "$value"
        ;;
      *)
        die "unexpected target resolver output: $assignment"
        ;;
    esac
  done <<< "$output"
  : "${SIM_TARGET_IMAGE:?target image was not resolved}"
  : "${SIM_TARGET_DOCKERFILE:?target dockerfile was not resolved}"
  : "${SIM_TARGET_CONTEXT:?target context was not resolved}"
  : "${SIM_TARGET_HERMETIC:?target hermetic flag was not resolved}"
  : "${SIM_TARGET_PLATFORMS:?target platforms was not resolved}"
  : "${SIM_TARGET_PIPELINE_RUN:?target PipelineRun was not resolved}"
}

docker_platform() {
  local platform="${1%%,*}"
  case "$platform" in
    linux/x86_64|linux/amd64) printf '%s\n' linux/amd64 ;;
    linux/aarch64|linux/arm64) printf '%s\n' linux/arm64 ;;
    *) die "unsupported Konflux build platform: $platform" ;;
  esac
}

run_static() {
  require_command python3
  python3 -c 'import yaml' >/dev/null 2>&1 || \
    die "PyYAML is required for static validation: python3 -m pip install pyyaml==6.0.3"
  python3 "$HELPER" static
}

run_hermetic() (
  require_command go
  require_command python3
  require_command tar

  local workdir src modcache builder actual ldflags toolchain
  workdir="$(mktemp -d)"
  trap 'chmod -R u+w "$workdir" 2>/dev/null || true; rm -rf "$workdir"' EXIT
  src="$workdir/src"
  modcache="$workdir/gomodcache"
  mkdir -p "$src" "$modcache"

  builder="$(python3 "$HELPER" go-version)"
  # CI installs the builder's Go release and uses local. A developer host may
  # only have an older base go binary with the matching toolchain cached, so
  # SIM_GOTOOLCHAIN can select it without changing the CI proof.
  toolchain="${SIM_GOTOOLCHAIN:-local}"
  export GOTOOLCHAIN="$toolchain"
  actual="$(go env GOVERSION)"
  case "$actual" in
    "go${builder}"|"go${builder}."*) ;;
    *)
      die "$(command -v go) provides $actual, but the Konflux builder is go-toolset:$builder; GOTOOLCHAIN=$toolchain cannot satisfy it (set SIM_GOTOOLCHAIN to the builder release on local hosts)"
      ;;
  esac
  info "builder Go $builder maps to runner $actual; GOTOOLCHAIN=$toolchain pins resolution"

  ldflags="-X github.com/opendatahub-io/mlflow-operator/internal/controller.SupportedMLflowVersion=$(python3 scripts/print_supported_mlflow_version.py --component-metadata config/component_metadata.yaml)"

  # Build from an isolated copy of the working tree. The live checkout is never
  # written, so a go.sum edit made during the run cannot be overwritten by an
  # EXIT trap and the lockfile checks still apply to this exact revision.
  (
    cd "$ROOT"
    tar \
      --exclude=.git \
      --exclude=.venv \
      --exclude=.venv-sim \
      --exclude=node_modules \
      --exclude='*mlflow-tests/.venv' \
      -cf - .
  ) | tar -xf - -C "$src"
  cp "$src/go.sum" "$workdir/root-go.sum"
  cp "$src/api/go.sum" "$workdir/api-go.sum"

  echo "=== Hermetic network phase: populate isolated module cache ==="
  (
    cd "$src"
    GOMODCACHE="$modcache" GOWORK=off GOFLAGS=-mod=readonly go mod download
  )
  (
    cd "$src/api"
    GOMODCACHE="$modcache" GOWORK=off GOFLAGS=-mod=readonly go mod download
  )
  if ! cmp -s "$src/go.sum" "$workdir/root-go.sum"; then
    die "go mod download rewrote go.sum; the lockfile is incomplete"
  fi
  if ! cmp -s "$src/api/go.sum" "$workdir/api-go.sum"; then
    die "go mod download rewrote api/go.sum; the lockfile is incomplete"
  fi
  info "module cache populated at $modcache"

  echo "=== Hermetic offline phase: GOPROXY=off for root and api modules ==="
  (
    cd "$src"
    GOMODCACHE="$modcache" GOWORK=off GOPROXY=off GONOPROXY=none \
      GOFLAGS=-mod=readonly CGO_ENABLED=0 GOOS=linux \
      go build -buildvcs=false -ldflags "$ldflags" -o "$workdir/manager" cmd/main.go
  )
  (
    cd "$src/api"
    GOMODCACHE="$modcache" GOWORK=off GOPROXY=off GONOPROXY=none \
      GOFLAGS=-mod=readonly CGO_ENABLED=0 GOOS=linux \
      go build -buildvcs=false ./...
  )
  test -s "$workdir/manager" || die "offline build produced no manager binary"
  ok "root module built cmd/main.go offline; api module built ./... offline"
  info "FIPS strictfipsruntime compilation is intentionally left to the operator image job; stock Go has no strictfipsruntime experiment"
)

smoke_operator_image() (
  local image="$1" help_output binary_dir container_id buildinfo
  help_output="$(mktemp)"
  binary_dir="$(mktemp -d)"
  container_id=""
  buildinfo="$binary_dir/buildinfo"
  # shellcheck disable=SC2329  # invoked from the EXIT trap
  cleanup_smoke() {
    rm -f "$help_output"
    if [ -n "$container_id" ]; then
      docker rm -f "$container_id" >/dev/null 2>&1 || true
    fi
    rm -rf "$binary_dir"
  }
  trap cleanup_smoke EXIT

  docker run --rm --entrypoint /manager "$image" --help >"$help_output" 2>&1 || {
    cat "$help_output" >&2
    die "operator image /manager --help failed"
  }
  grep -q -- '-metrics-bind-address' "$help_output" || {
    cat "$help_output" >&2
    die "operator image /manager --help did not expose the manager flags"
  }
  docker run --rm --entrypoint sh "$image" -c '
    set -euo pipefail
    test -x /manager
    test -f /charts/mlflow/Chart.yaml
    test -f /charts/mlflow/values.yaml
    test "$(id -u)" = "1001"
  ' || die "operator image assets/user contract failed"

  # Assert the effective build settings recorded in the packaged binary. Both
  # FIPS strings can remain in the Dockerfile while CGO_ENABLED=0 selects the
  # non-FIPS branch, so source text alone is not proof.
  require_command go
  container_id="$(docker create "$image")"
  docker cp "$container_id:/manager" "$binary_dir/manager" >/dev/null
  docker rm -f "$container_id" >/dev/null
  container_id=""
  go version -m "$binary_dir/manager" > "$buildinfo"
  if ! grep -Eq '^[[:space:]]*build[[:space:]]+CGO_ENABLED=1[[:space:]]*$' "$buildinfo"; then
    cat "$buildinfo" >&2
    die "packaged /manager was not built with CGO_ENABLED=1; the non-FIPS branch was selected"
  fi
  if ! grep -Eq '^[[:space:]]*build[[:space:]]+GOEXPERIMENT=.*strictfipsruntime' "$buildinfo"; then
    cat "$buildinfo" >&2
    die "packaged /manager does not record GOEXPERIMENT=strictfipsruntime"
  fi
  ok "operator image smoke passed: /manager runs with CGO_ENABLED=1 strictfipsruntime, charts/mlflow assets exist, user=1001"
)

smoke_tests_image() {
  local image="$1" expected="$2"
  docker run --rm --entrypoint bash -e EXPECTED_MLFLOW_VERSION="$expected" "$image" -c '
    set -euo pipefail
    bash -n mlflow-tests/images/test-run.sh
    test -d mlflow-tests/src
    test -f .github/actions/deploy/deploy.py
    test -f .github/test-infra/overlays/kind/generate-tls.sh
    test -f .github/workflows/trace-analytics-prepopulation.yml
    test -f config/component_metadata.yaml
    test -f scripts/print_supported_mlflow_version.py
    test -f mlflow-tests/.bash_env
    test -x mlflow-tests/.venv/bin/python
    test -x /usr/local/bin/oc
    test -x /usr/local/bin/kustomize
    command -v uv >/dev/null
    test "${MLFLOW_TEST_SUPPORTED_VERSION:-}" = "$EXPECTED_MLFLOW_VERSION"
    mlflow-tests/.venv/bin/python -c '\''
import os
import mlflow

expected = os.environ["EXPECTED_MLFLOW_VERSION"]
actual = ".".join(mlflow.__version__.split(".")[:2])
if actual != expected:
    raise SystemExit(f"mlflow version mismatch: image has {mlflow.__version__}, expected {expected}.x")
print(f"mlflow {mlflow.__version__} aligns with supported {expected}.x")
'\''
  ' || die "mlflow-tests image smoke failed"
  ok "mlflow-tests image smoke passed: entrypoint syntax, assets, oc/kustomize/uv and MLflow $expected.x"
}

run_image() {
  local role="${1:?usage: validate-build.sh image <operator|tests>}" platform expected
  require_command docker
  load_target "$role"
  docker info >/dev/null 2>&1 || die "Docker daemon is not available"

  platform="$(docker_platform "$SIM_TARGET_PLATFORMS")"
  echo "=== Building $role image from Konflux target ==="
  info "pipelineRun=$SIM_TARGET_PIPELINE_RUN dockerfile=$SIM_TARGET_DOCKERFILE context=$SIM_TARGET_CONTEXT platform=$platform"
  info "image=$SIM_TARGET_IMAGE"
  docker build --platform "$platform" -f "$SIM_TARGET_DOCKERFILE" -t "$SIM_TARGET_IMAGE" "$SIM_TARGET_CONTEXT"

  case "$role" in
    operator)
      smoke_operator_image "$SIM_TARGET_IMAGE"
      ;;
    tests)
      expected="$(python3 scripts/print_supported_mlflow_version.py --component-metadata config/component_metadata.yaml --normalized)"
      smoke_tests_image "$SIM_TARGET_IMAGE" "$expected"
      ;;
    *)
      die "unknown image role: $role"
      ;;
  esac
}

run_operator_integration() (
  local image="${OPERATOR_IMAGE:?set OPERATOR_IMAGE to the PR-built operator image}"
  local kind_cluster="${KIND_CLUSTER_NAME:?set KIND_CLUSTER_NAME to the Kind cluster name}"
  local namespace="${NAMESPACE:-opendatahub}"
  local mlflow_image="${MLFLOW_IMAGE:?set MLFLOW_IMAGE to the sample MLflow image}"
  local context="kind-${kind_cluster}"
  local monitor_name="mlflow-operator-controller-manager-metrics-monitor"
  local service_monitor_name="mlflow-metrics-monitor"
  local controller_deployment="mlflow-operator-controller-manager"
  local operator_pod="" pod="" value="" current_context=""
  local private_kubeconfig="" memory_limit="" patch=""
  local params_env="$ROOT/.github/test-infra/overlays/kind/params.env"
  local backup_dir
  backup_dir="$(mktemp -d)"
  cp "$params_env" "$backup_dir/params.env"

  # shellcheck disable=SC2329  # invoked from the EXIT trap
  dump_diagnostics() {
    echo "=== Kind diagnostics (namespace $namespace, context $context) ===" >&2
    k -n "$namespace" get deployment,pods,svc,servicemonitor -o wide >&2 2>&1 || true
    k get mlflowoperator default-mlflowoperator -o yaml >&2 2>&1 || true
    k get mlflow mlflow -o yaml >&2 2>&1 || true
    k -n "$namespace" get events --sort-by=.lastTimestamp >&2 2>&1 | tail -n 60 || true
    k -n "$namespace" logs "deployment/$controller_deployment" --tail=100 >&2 2>&1 || true
    k -n "$namespace" logs deployment/mlflow --tail=100 >&2 2>&1 || true
  }

  # shellcheck disable=SC2329  # invoked from the EXIT trap
  cleanup() {
    local status=$?
    trap - EXIT
    cp "$backup_dir/params.env" "$params_env" 2>/dev/null || true
    if [ -n "$private_kubeconfig" ]; then
      rm -f "$private_kubeconfig"
    fi
    rm -f "$ROOT/.github/test-infra/overlays/kind/tls.crt" "$ROOT/.github/test-infra/overlays/kind/tls.key"
    rm -rf "$backup_dir"
    if [ "$status" -ne 0 ]; then
      dump_diagnostics || true
    fi
    exit "$status"
  }
  trap cleanup EXIT

  require_command kubectl
  require_command python3
  require_command kustomize
  require_command envsubst
  require_command openssl
  python3 -c 'import yaml' >/dev/null 2>&1 || die "PyYAML is required for deploy.py"
  python3 -c 'import kubernetes' >/dev/null 2>&1 || die "the kubernetes Python package is required for deploy.py"

  load_target operator
  assert_nonempty_eq "operator image matches the Tekton target" "$SIM_TARGET_IMAGE" "$image"

  k() {
    kubectl --context "$context" "$@"
  }

  # deploy.py does not accept a context flag, so both it and the assertions
  # must point at the same cluster. Fail before mutating either one.
  current_context="$(kubectl config current-context 2>/dev/null || true)"
  if [ "$current_context" != "$context" ]; then
    die "kubectl current-context is '${current_context:-<unset>}', expected '$context'; deploy.py would target the wrong cluster"
  fi

  # deploy.py calls kubectl without --context. Give it a private kubeconfig
  # that contains only the selected context so a concurrent change to the
  # shared current-context cannot redirect its writes to another cluster.
  private_kubeconfig="$(mktemp)"
  chmod 600 "$private_kubeconfig"
  kubectl config view --minify --flatten --context "$context" > "$private_kubeconfig" || \
    die "could not build a private kubeconfig for context '$context'"

  echo "=== Installing Kind prerequisites for the MLflowOperator module path ==="
  k apply -f "$ROOT/config/crd/bases"
  k apply -f "$ROOT/config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml"
  k apply -f "$ROOT/test/crd/monitoring.coreos.com_servicemonitors.yaml"
  k apply -f "$ROOT/test/crd/httproutes.gateway.networking.k8s.io.yaml"
  k wait --for=condition=Established --timeout=180s \
    crd/mlflowoperators.components.platform.opendatahub.io \
    crd/mlflows.mlflow.opendatahub.io \
    crd/mlflowconfigs.mlflow.kubeflow.org \
    crd/servicemonitors.monitoring.coreos.com \
    crd/httproutes.gateway.networking.k8s.io

  # Kind normally ships a default `standard` StorageClass backed by
  # rancher.io/local-path, which is the normal PVC provisioning path. Only when
  # that class is absent (some older Kind releases) fall back to a static PV so
  # the repo's minimal sample can still exercise its PVC path.
  if ! k get storageclass standard >/dev/null 2>&1; then
    k apply -f - <<'EOF'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: standard
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: Immediate
reclaimPolicy: Retain
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: mlflow-pr-validation-pv
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: standard
  hostPath:
    path: /tmp/mlflow-pr-validation
EOF
  fi
  k annotate storageclass standard storageclass.kubernetes.io/is-default-class=true --overwrite >/dev/null

  echo "=== Deploying the PR-built operator image with the repo deploy helper ==="
  KUBECONFIG="$private_kubeconfig" python3 "$ROOT/.github/actions/deploy/deploy.py" \
    --namespace "$namespace" \
    --mlflow-image "$mlflow_image" \
    --mlflow-operator-image "$image" \
    --skip-mlflow-cr
  k -n "$namespace" rollout status "deployment/$controller_deployment" --timeout=300s

  value="$(k -n "$namespace" get "deployment/$controller_deployment" -o jsonpath='{.spec.template.spec.containers[?(@.name=="manager")].image}' 2>/dev/null || true)"
  assert_nonempty_eq "operator deployment image" "$image" "$value"

  echo "=== Enabling the MLflowOperator module controller and creating its singleton CR ==="
  k -n "$namespace" set env "deployment/$controller_deployment" \
    ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER=true \
    APPLICATIONS_NAMESPACE="$namespace"
  k -n "$namespace" rollout status "deployment/$controller_deployment" --timeout=300s

  operator_pod="$(k -n "$namespace" get pods -l control-plane=controller-manager --sort-by=.metadata.creationTimestamp -o custom-columns=NAME:.metadata.name --no-headers | awk 'NF {name=$1} END {print name}')"
  [ -n "$operator_pod" ] || die "no controller-manager pod found after enabling the module controller"
  assert_nonempty_eq "operator pod phase" "Running" "$(k -n "$namespace" get pod "$operator_pod" -o jsonpath='{.status.phase}')"
  assert_nonempty_eq "operator pod Ready" "True" "$(k -n "$namespace" get pod "$operator_pod" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  value="$(k -n "$namespace" get pod "$operator_pod" -o jsonpath='{.status.containerStatuses[0].image}')"
  assert_nonempty_eq "operator pod image" "$image" "$value"
  assert_nonempty_eq "operator pod restartCount" "0" "$(k -n "$namespace" get pod "$operator_pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')"

  k apply -f - <<'EOF'
apiVersion: components.platform.opendatahub.io/v1alpha1
kind: MLflowOperator
metadata:
  name: default-mlflowoperator
spec:
  gatewayName: data-science-gateway
  sectionTitle: OpenShift Open Data Hub
EOF
  k wait --for=condition=Ready mlflowoperator/default-mlflowoperator --timeout=300s

  value="$(k get mlflowoperator default-mlflowoperator -o jsonpath='{.metadata.finalizers}')"
  case "$value" in
    *mlflow.opendatahub.io/mlflow-operator-protection*) ok "MLflowOperator finalizer is present" ;;
    *) die "MLflowOperator finalizer is absent: '$value'" ;;
  esac

  echo "=== Asserting the operator self-metrics ServiceMonitor ==="
  for _ in $(seq 1 60); do
    if k -n "$namespace" get servicemonitor "$monitor_name" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  k -n "$namespace" get servicemonitor "$monitor_name" >/dev/null 2>&1 || \
    die "operator self-metrics ServiceMonitor $monitor_name was not created"
  assert_nonempty_eq "operator ServiceMonitor scheme" "https" "$(k -n "$namespace" get servicemonitor "$monitor_name" -o jsonpath='{.spec.endpoints[0].scheme}')"
  assert_nonempty_eq "operator ServiceMonitor port" "https" "$(k -n "$namespace" get servicemonitor "$monitor_name" -o jsonpath='{.spec.endpoints[0].port}')"
  assert_nonempty_eq "operator ServiceMonitor path" "/metrics" "$(k -n "$namespace" get servicemonitor "$monitor_name" -o jsonpath='{.spec.endpoints[0].path}')"

  echo "=== Applying the repository MLflow sample and asserting readiness ==="
  k apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mlflow-sa
  namespace: $namespace
automountServiceAccountToken: false
EOF
  k -n "$namespace" get secret mlflow-tls >/dev/null 2>&1 || die "deploy.py did not create the mlflow-tls secret"
  k apply -f "$ROOT/config/samples/mlflow_v1_mlflow_minimal.yaml"

  # The sample hardcodes the mutable odh-stable tag and a 512Mi limit that is
  # below MLflow's startup working set in Kind (OOMKilled). Patch the CR, not
  # the generated Deployment, so reconciliation keeps the digest-pinned operand
  # image and the repo e2e suite's proven 1Gi/3Gi ceiling.
  patch="$(python3 - "$mlflow_image" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "spec": {
                "image": {"image": sys.argv[1]},
                "resources": {
                    "requests": {"cpu": "500m", "memory": "1Gi"},
                    "limits": {"cpu": "2", "memory": "3Gi"},
                },
            }
        }
    )
)
PY
)"
  k patch mlflow mlflow --type=merge -p "$patch"

  for _ in $(seq 1 120); do
    if k -n "$namespace" get deployment mlflow >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  k -n "$namespace" get deployment mlflow >/dev/null 2>&1 || die "MLflow deployment was not created"

  # A reused Kind cluster can still report the previous run's Available=True
  # because the MLflow condition has no observedGeneration. Wait until
  # reconciliation has applied the patched limit before trusting readiness.
  for _ in $(seq 1 60); do
    memory_limit="$(k -n "$namespace" get deployment mlflow -o jsonpath='{.spec.template.spec.containers[?(@.name=="mlflow")].resources.limits.memory}' 2>/dev/null || true)"
    if [ "$memory_limit" = "3Gi" ]; then
      break
    fi
    sleep 5
  done
  assert_nonempty_eq "MLflow deployment memory limit" "3Gi" "$memory_limit"

  k -n "$namespace" rollout status deployment/mlflow --timeout=900s
  k wait --for=condition=Available mlflow/mlflow --timeout=900s
  assert_nonempty_eq "MLflow Available" "True" "$(k get mlflow mlflow -o jsonpath='{.status.conditions[?(@.type=="Available")].status}')"
  assert_nonempty_eq "MLflow MLflowOperatorReady" "True" "$(k get mlflow mlflow -o jsonpath='{.status.conditions[?(@.type=="MLflowOperatorReady")].status}')"

  assert_nonempty_eq "MLflow CR image matches the configured operand image" "$mlflow_image" "$(k get mlflow mlflow -o jsonpath='{.spec.image.image}')"
  value="$(k -n "$namespace" get deployment mlflow -o jsonpath='{.spec.template.spec.containers[0].image}')"
  assert_nonempty_eq "MLflow deployment image" "$mlflow_image" "$value"

  pod="$(k -n "$namespace" get pods -l app=mlflow --sort-by=.metadata.creationTimestamp -o custom-columns=NAME:.metadata.name --no-headers | awk 'NF {name=$1} END {print name}')"
  [ -n "$pod" ] || die "no MLflow pod matched app=mlflow"
  assert_nonempty_eq "MLflow pod Ready" "True" "$(k -n "$namespace" get pod "$pod" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  assert_nonempty_eq "MLflow pod image" "$mlflow_image" "$(k -n "$namespace" get pod "$pod" -o jsonpath='{.spec.containers[0].image}')"
  assert_nonempty_eq "MLflow pod restartCount" "0" "$(k -n "$namespace" get pod "$pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')"

  echo "=== Asserting the MLflow chart ServiceMonitor ==="
  for _ in $(seq 1 60); do
    if k -n "$namespace" get servicemonitor "$service_monitor_name" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  k -n "$namespace" get servicemonitor "$service_monitor_name" >/dev/null 2>&1 || \
    die "MLflow chart ServiceMonitor $service_monitor_name was not created"
  assert_nonempty_eq "MLflow ServiceMonitor scheme" "https" "$(k -n "$namespace" get servicemonitor "$service_monitor_name" -o jsonpath='{.spec.endpoints[0].scheme}')"
  assert_nonempty_eq "MLflow ServiceMonitor port" "https" "$(k -n "$namespace" get servicemonitor "$service_monitor_name" -o jsonpath='{.spec.endpoints[0].port}')"
  assert_nonempty_eq "MLflow ServiceMonitor path" "/metrics" "$(k -n "$namespace" get servicemonitor "$service_monitor_name" -o jsonpath='{.spec.endpoints[0].path}')"

  ok "MLflowOperator and MLflow are Ready; both ServiceMonitors exist with lowercase https"
)

usage() {
  cat <<'EOF'
Usage: scripts/validate-build.sh <command> [args]

Commands:
  static                 Validate Tekton, Dockerfile, image-content and version contracts
  go-version             Print the Go version from the Konflux builder image
  hermetic               Populate an isolated module cache, then build root and api with GOPROXY=off
  image <operator|tests> Build the Konflux target and run its runtime smoke test
  operator               Deploy the PR image to Kind and assert MLflowOperator/MLflow + ServiceMonitors
  assert-image EXPECTED ACTUAL
                         Fail unless both values are non-empty and equal (used by the Kind phase)
  targets                Print resolved Konflux PR targets as JSON
EOF
}

main() {
  local command="${1:-}"
  shift || true
  case "$command" in
    static) run_static ;;
    go-version) python3 "$HELPER" go-version ;;
    hermetic) run_hermetic ;;
    image) run_image "${1:-}" ;;
    operator) run_operator_integration ;;
    targets) python3 "$HELPER" targets ;;
    assert-image)
      assert_nonempty_eq "rendered image" "${1:-}" "${2:-}"
      ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; die "unknown command: $command" ;;
  esac
}

main "$@"
