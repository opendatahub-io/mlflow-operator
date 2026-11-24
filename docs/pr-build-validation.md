# PR Build Validation (Konflux Simulator)

`.github/workflows/pr-build-validation.yml` simulates the two Konflux PR builds
declared in this repository, then proves the operator image can reconcile the
repo's own `MLflowOperator` module CR and an `MLflow` sample in Kind.

The workflow is intentionally narrower than the existing test suites. It does
not re-run unit tests, the full Kind e2e suite, the integration matrix, or the
code-generation workflows; it validates the build inputs and packaging
contracts those suites do not cover.

## Konflux Targets

The workflow resolves these values from `.tekton/*pull-request.yaml` on every
run. Dockerfile paths are resolved relative to the Tekton `path-context` and
then passed repo-relative to Docker, so neither the workflow nor the static
checks hardcode a file name.

| Target | PipelineRun | Image | Dockerfile | Context | Hermetic | Platforms |
|---|---|---|---|---|---|---|
| Operator | `.tekton/mlflow-operator-pull-request.yaml` | `quay.io/opendatahub/mlflow-operator:odh-pr` | `Dockerfile` | `.` | `false` (pipeline default) | `linux/x86_64` (pipeline default) |
| MLflow tests | `.tekton/mlflow-tests-pull-request.yaml` | `quay.io/opendatahub/mlflow-tests:odh-pr` | `mlflow-tests/images/Dockerfile.konflux` | `.` | `false` (explicit) | `linux/x86_64` (pipeline default) |

Both PipelineRuns delegate to
`odh-konflux-central/pipeline/multi-arch-container-build.yaml`. Despite the
pipeline name, its current default is one platform, `linux/x86_64`; the
simulator maps that to Docker's `linux/amd64`.

## Jobs

| Job | What it proves |
|---|---|
| `Check Skip Condition` | Evaluates the single skip gate once and publishes `should-skip`/`reason`. |
| `Resolve Konflux Targets` | Parses both PR PipelineRuns and exposes their image, Dockerfile, context, hermetic and platform values to later jobs. |
| `Static Build Contracts` | Tekton inputs, COPY sources, image content/entrypoint contracts, Go module alignment, supported-version alignment, ServiceMonitor scheme, generator delegation and Kind prerequisites. |
| `Hermetic Build Preflight` | Populates one isolated module cache, then builds root `cmd/main.go` and the `api` module with `GOPROXY=off`. |
| `Build and Validate Operator Image` | Builds the exact Tekton operator target, runs `/manager --help`, checks `/charts/mlflow`, and uploads the image artifact. |
| `Build and Validate MLflow Tests Image` | Builds the exact Tekton tests target, checks the entrypoint syntax, assets, tools, baked `MLFLOW_TEST_SUPPORTED_VERSION`, and the locked MLflow version. |
| `Operator Integration (Kind)` | Deploys the PR-built operator, enables the module controller, applies `MLflowOperator` and the repo MLflow sample, and asserts readiness plus both ServiceMonitors. |
| `Build Validation Summary` | Fails if skip evaluation failed, if any validation job did not run, or if a skipped job did not skip cleanly. This is the only check that should be required in branch protection. |

The workflow has no path filter, pinned action SHAs, `persist-credentials:
false` on every checkout, read-only job permissions, and no PR-controlled text
inside a shell script. It has no matrix, so `fail-fast` does not apply.

## Static Contracts

`scripts/validate-build.sh static` runs `scripts/validate_tekton_contracts.py`.
The helper checks:

- Every PR PipelineRun has non-empty `output-image`, `dockerfile`, and
  `path-context`; the Dockerfile exists under that context and does not escape
  the repository. The resolved path is used by every later Dockerfile check.
- Both PipelineRuns use the expected external pipeline and resolve to the
  pipeline's single non-hermetic `linux/x86_64` platform. A multi-arch change
  must be reflected here deliberately.
- Every local `COPY`/`ADD` source, including Dockerfile JSON-array operands,
  exists under the Tekton context; globs cannot escape it. `COPY --from=...`
  sources are checked by the image-content assertions instead.
- `.dockerignore` is evaluated in order with Docker's last-match-wins and
  negation semantics for every root both Dockerfiles consume.
- The operator final image copies `/workspace/manager` to `/manager` and
  `/workspace/charts/mlflow` to `charts/mlflow`, runs as UID 1001, and selects
  the effective FIPS branch (`CGO_ENABLED=1`, `GOEXPERIMENT` and `-tags
  strictfipsruntime`) for the binary it packages. The image smoke test also
  reads the binary's Go build info to prove those settings were applied.
- The tests image uses the `bash mlflow-tests/images/test-run.sh` entrypoint,
  runs as UID 1001, installs with `uv sync --locked`, bakes
  `MLFLOW_TEST_SUPPORTED_VERSION` into `BASH_ENV`, and copies the test assets,
  deploy helper and test-infra it needs.
- The root and `api` modules align with the builder Go version parsed from the
  resolved operator Dockerfile, the local `replace` directive exists, and both
  `go.sum` files are present.
- `config/component_metadata.yaml`, the Makefile, both Dockerfiles,
  `mlflow-tests/uv.lock`, and the current `pre_upgrade`/`post_upgrade` test
  files agree on the supported MLflow minor version.
- The operator's self-metrics ServiceMonitor uses lowercase
  `monitoringv1.Scheme("https")`; the chart ServiceMonitor uses `scheme: https`
  and `path: /metrics`.
- The ServiceMonitor CRD fixture accepts exactly `http` and `https`. Its source
  annotation is checked against the `prometheus-operator` API version in
  `go.mod`, and its enum is intentionally tighter than the generated v0.89
  schema, which listed uppercase aliases and masked RHOAIENG-94659/94654.

### Generated Code And Manifests

This repository already runs the generator checks on PRs:

- `.github/workflows/verify-codegen.yml` runs `make manifests`, `make generate`
  and `git diff --exit-code`.
- `.github/workflows/verify-kustomize.yml` runs `make verify-manifests`.

The simulator does **not** copy those commands. The static contract fails if
either workflow stops running on `pull_request` or stops containing the exact
`run:` command it owns, so a future change cannot silently remove the drift
check while this workflow still passes.

## Hermetic Preflight

The Docker builder is `registry.access.redhat.com/ubi9/go-toolset:1.26`. The
workflow parses `1.26` from the Tekton-selected Dockerfile, installs it with
`actions/setup-go`, and runs with `GOTOOLCHAIN=local`, matching the builder's
no-download behavior. The preflight:

1. Copies the working tree, including uncommitted edits, into a temporary
   source tree and creates an isolated `GOMODCACHE` outside it.
2. Runs plain `go mod download` for the root and `api` modules in that copy
   with network access. It does not use `all`, which can rewrite `go.sum`.
3. Fails if the cache-population step changes either `go.sum`. The developer's
   checkout is never written, so the check cannot overwrite an edit made while
   the preflight runs.
4. Builds root `cmd/main.go` and the `api` module from the isolated tree with
   `GOPROXY=off` and `GONOPROXY=none`, using the supported-version `-ldflags`
   value from the Makefile.

The preflight uses `CGO_ENABLED=0` because stock upstream Go does not implement
Red Hat's `strictfipsruntime` experiment. The exact FIPS compile is covered by
the operator image build, which uses the Red Hat toolset image and Dockerfile
flags.

On a developer host, put the builder's Go release first on `PATH` and set
`SIM_GOTOOLCHAIN=local`. Setting `GOTOOLCHAIN=go1.26.0` while also using an
isolated `GOMODCACHE` makes Go try to download the toolchain into that empty
cache before it can run `go mod download`.

## Operator Integration

The Kind phase deliberately asserts this repository's own module condition, not
`DataScienceCluster` coverage. The operator owns `MLflowOperator` reconciliation;
the platform operator owns DSC propagation.

The phase installs the repo CRDs plus the tightened ServiceMonitor and HTTPRoute
fixtures, then deploys the PR-built operator with
`.github/actions/deploy/deploy.py`. The ServiceMonitor CRD is installed
**before** the operator starts because the operator discovers that CRD at
startup. The phase then:

1. Rejects a missing, empty, or non-Tekton `OPERATOR_IMAGE` before deployment.
2. Enables `ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER=true` and
   `APPLICATIONS_NAMESPACE=opendatahub`.
3. Creates `MLflowOperator/default-mlflowoperator` and waits for `Ready=True`.
4. Applies `config/samples/mlflow_v1_mlflow_minimal.yaml`.
5. Patches the **MLflow CR**, not its generated Deployment, to the repo e2e
   suite's 1Gi/3Gi memory envelope. The sample's 512Mi limit is OOM-killed in
   Kind; patching the Deployment alone is reverted by reconciliation.
6. Waits until the generated Deployment's `mlflow` container actually shows the
   patched 3Gi limit, then waits for `MLflow/mlflow` to become
   `Available=True` and asserts `MLflowOperatorReady=True`.
7. Asserts both `ServiceMonitor` resources exist and use `scheme: https`,
   `port: https` and `/metrics`.
8. Asserts the operator Deployment, operator Pod and MLflow Pod use the expected
   image, are Ready, and have zero restarts.

The configured Kind release normally installs a default `standard`
StorageClass backed by `rancher.io/local-path`, so that dynamic provisioner is
the normal CI path and stores PVC data under `/var/local-path-provisioner` on
the Kind node. The phase creates a static 10Gi `hostPath` PV and a
`no-provisioner` `standard` class only when that built-in class is unavailable.
If a PVC stays Pending, check which of those two paths is active before
assuming the static PV was created. The phase does not deploy an OpenShift
service CA, ConsoleLink or the ODH platform operator. The operator tolerates
the missing OpenShift API and ConsoleLink CRD; those prerequisites are not
needed for this module handoff assertion.

### Historical Evidence

The supplied history is classified here. No Jira client is available in this
environment, so only the local fix commit and the local runtime replay were
independently verified.

| Evidence | Local verification | Covered by | Classification |
|---|---|---|---|
| RHOAIENG-94659 / RHOAIENG-94654: uppercase ServiceMonitor scheme rejected by the Prometheus CRD; `MLflowOperator` never became Ready and DSC was blocked | Commit `19702f7` changed `monitoringv1.SchemeHTTPS` to `monitoringv1.Scheme("https")`. A temporary rebuild with `SchemeHTTPS` timed out waiting for `MLflowOperator`; the operator log reported `Unsupported value: "HTTPS": supported values: "http", "https"`. | Static scheme contract, Kind readiness and ServiceMonitor assertions | Operator/build path covered here; DSC propagation remains operator-only. |
| RHOAIENG-85615: module operators including MLflow fail to deploy | Not present in local history. | Nothing in this simulator | Operator-only: needs the ODH module handler/platform deployment path, not a standalone operator image. |
| RHOAIENG-79314: DSC GC race deletes the managed `MLflowOperator` CR | Not present in local history. | Nothing in this simulator | Operator-only: needs platform GC/DSC reconciliation. |
| RHOAIENG-95836: bundle missing module CRDs | Not present in local history. | Repo CRD existence only; no bundle/CSV assertion | Operator-only: bundle generation and consumer validation. |
| QE test names `testMlflowExperiments`, `testMlflowAgentObservability` | Not referenced in this checkout. | Nothing claimed | Operator/platform e2e owner; no description is inferred here. |

## Verification Performed

The following commands and results were run in this working tree:

```text
python3 -m py_compile scripts/validate_tekton_contracts.py      PASS
bash -n scripts/validate-build.sh                               PASS
shellcheck scripts/validate-build.sh                            PASS
actionlint .github/workflows/pr-build-validation.yml            PASS
./scripts/validate-build.sh static                              PASS

PATH="/path/to/go1.26.0/bin:$PATH" SIM_GOTOOLCHAIN=local \
  ./scripts/validate-build.sh hermetic
✅ root module built cmd/main.go offline; api module built ./... offline

./scripts/validate-build.sh image operator
✅ operator image smoke passed: /manager runs, charts/mlflow assets exist, user=1001

./scripts/validate-build.sh image tests
✅ mlflow-tests image smoke passed: entrypoint syntax, assets, oc/kustomize/uv and MLflow 3.17.x
```

The image-smoke line above reflects the assertions at the time of the local
run. The operator image smoke now also reads the packaged `/manager` Go build
info and requires `CGO_ENABLED=1` and `GOEXPERIMENT=strictfipsruntime`; the
operator-image CI job is the authoritative proof for that assertion.

The image jobs built the exact `linux/amd64` targets from Tekton. The local
Kind pass rebuilt the same operator Dockerfile for the native arm64 node and
reported:

```text
✅ operator deployment image=quay.io/opendatahub/mlflow-operator:odh-pr
✅ operator pod Ready=True
✅ operator pod restartCount=0
✅ MLflowOperator finalizer is present
✅ operator ServiceMonitor scheme=https
✅ MLflow Available=True
✅ MLflow MLflowOperatorReady=True
✅ MLflow pod Ready=True
✅ MLflow pod restartCount=0
✅ MLflow ServiceMonitor scheme=https
✅ MLflowOperator and MLflow are Ready; both ServiceMonitors exist with lowercase https
```

The exact amd64 Kind job has not been run locally; it is the GitHub Actions job
defined by this workflow.

## Fault-Injection Proof

Each mutation was applied temporarily and reverted. The working tree was checked
with `git diff` afterward; no injection remains.

| Check | Mutation | Observed failure |
|---|---|---|
| Tekton path resolution | Renamed `Dockerfile` to `Containerfile`, updated Tekton and `.dockerignore` | Valid rename passed; changing its entrypoint failed with `Containerfile: ENTRYPOINT must be ["/manager"] in the final image` |
| Tekton context boundary | Changed `path-context` from `.` to `..` | `❌ .tekton/mlflow-operator-pull-request.yaml: path-context escapes the repository: ..` |
| COPY source existence | Changed `COPY go.sum go.sum` to `go.sum.missing` | `❌ Dockerfile: COPY source does not exist: go.sum.missing` |
| Required module manifests | Removed `COPY api/go.sum api/go.sum` | `❌ Dockerfile: module manifests must be COPYed before 'go mod download': api/go.sum` |
| Operator entrypoint | Changed the final `ENTRYPOINT` | `❌ Dockerfile: ENTRYPOINT must be ["/manager"] in the final image` |
| Tests entrypoint | Pointed at a nonexistent test runner | `❌ mlflow-tests/images/Dockerfile.konflux: ENTRYPOINT must be ['bash', 'mlflow-tests/images/test-run.sh']` |
| Builder Go alignment | Changed `go-toolset:1.26` to `1.24` | `❌ Dockerfile builder Go 1.24 is older than go.mod go 1.26.0; GOTOOLCHAIN=local would fail` |
| Docker ignore inputs | Removed `!go.sum` | `❌ .dockerignore does not re-include required build inputs: go.sum` |
| ServiceMonitor Go scheme | Changed to `monitoringv1.SchemeHTTPS` | `❌ operator self-metrics ServiceMonitor must use monitoringv1.Scheme("https")` |
| ServiceMonitor fixture | Re-added `HTTP`/`HTTPS` to the CRD enum | `❌ test/crd/...: scheme enum must be exactly ['http', 'https'] ...` |
| Supported MLflow version | Changed metadata to `v3.18.0` | `❌ supported MLflow version drift: component metadata normalizes to 3.18, mlflow-tests/uv.lock pins 3.17.0` |
| Generator delegation | Removed `make generate` from `verify-codegen.yml` | `❌ ... missing a run step for 'make generate'` |
| Module controller default | Changed `ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER` to `true` | `❌ config/manager/manager.yaml: module controller must stay disabled by default` |
| Offline Go build | Added an unavailable import to `cmd/main.go` | `cmd/main.go:60:2: cannot find module providing package example.com/simulator-unavailable/module: import lookup disabled by -mod=readonly` |
| Rendered image identity | Passed `:latest` instead of the PR tag | `❌ rendered image mismatch: expected '...:odh-pr', got '...:latest'` |
| Rendered image absence | Passed an empty actual or expected value | `❌ rendered image actual value is absent/empty` / `❌ rendered image expected value is empty` |
| CRD runtime rejection | Applied a generated ServiceMonitor with `scheme: HTTPS` | `The ServiceMonitor "uppercase-scheme-fault" is invalid: spec.endpoints[0].scheme: Unsupported value: "HTTPS": supported values: "http", "https"` |
| Kind integration | Built and deployed the uppercase-scheme operator | `error: timed out waiting for the condition on mlflowoperators/default-mlflowoperator`; operator log repeated `reconcile metrics ServiceMonitor ... Unsupported value: "HTTPS"` |

The summary job was executed directly from the workflow YAML in six cases:
all-success, deliberate skip, leaky skip, gate failure, empty skip decision,
and a failed validation. The exit statuses were `0, 0, 1, 1, 1, 1` as expected.

## Local Usage

Requirements: Python 3.11 or newer (the contract parser imports `tomllib`) and
a Go release matching the builder for the hermetic and image-smoke checks.

```bash
python3.11 -m venv .venv-sim
. .venv-sim/bin/activate
python3 -m pip install "pyyaml==6.0.3" "kubernetes==33.1.0"

./scripts/validate-build.sh static
./scripts/validate-build.sh go-version
PATH="/path/to/go1.26.0/bin:$PATH" SIM_GOTOOLCHAIN=local ./scripts/validate-build.sh hermetic

# Exact Tekton architecture and image smoke tests.
./scripts/validate-build.sh image operator
./scripts/validate-build.sh image tests
```

The Kind phase expects an existing cluster, the PR image loaded into it, and
`kustomize`, `envsubst`, `openssl`, the deploy Python packages and `kubectl` on
`PATH`. The build below uses the host architecture; on an amd64 host it is the
exact target, while an arm64 host produces a runtime-equivalent local image:

```bash
while IFS= read -r assignment; do
  export "$assignment"
done < <(python3 scripts/validate_tekton_contracts.py target-env operator)
export KIND_CLUSTER_NAME=mlflow-pr-local
export MLFLOW_IMAGE=quay.io/opendatahub/mlflow:odh-stable@sha256:5a7e2a217634b5d73d6e707e79133fbcadb0277a9753c846ed58fa97220efc4c

docker build -f "$SIM_TARGET_DOCKERFILE" -t "$SIM_TARGET_IMAGE" "$SIM_TARGET_CONTEXT"
kind create cluster --name "$KIND_CLUSTER_NAME" \
  --image 'kindest/node:v1.33.7@sha256:d26ef333bdb2cbe9862a0f7c3803ecc7b4303d8cea8e814b481b09949d353040'
kind load docker-image "$SIM_TARGET_IMAGE" --name "$KIND_CLUSTER_NAME"
OPERATOR_IMAGE="$SIM_TARGET_IMAGE" ./scripts/validate-build.sh operator
```

The script refuses to run unless `kubectl config current-context` is exactly
`kind-$KIND_CLUSTER_NAME`. It then gives `deploy.py` a private kubeconfig that
contains only that context, because `deploy.py` does not accept a context flag.

## CI Proof Plan

If Kind cannot run on a developer host, the exact architecture proof is the
`Operator Integration (Kind)` job:

1. `Build and Validate Operator Image` builds the Tekton-selected
   `linux/x86_64` target and uploads the image artifact.
2. `Operator Integration (Kind)` creates a pinned amd64 Kind node, loads that
   artifact under the resolved Tekton image name, and runs the same phase shown
   above.
3. A wrong image is rejected before deployment by the Tekton-target equality
   check. An uppercase scheme is rejected first by static validation; direct
   execution of the image and Kind jobs also reproduces the NotReady path.

## Limits

- The exact amd64 image builds and smoke tests ran locally, and the Kind phase
  passed on a native arm64 rebuild of the same source. The CI amd64 Kind job has
  not been executed here.
- The Kind phase does not install OpenShift service CA, ConsoleLink, the Auth
  CRD or the ODH platform operator. It relies on Kind's default
  `rancher.io/local-path` StorageClass (or a static PV fallback when absent)
  and mocks only the ServiceMonitor and HTTPRoute schemas plus the MLflow
  service account/TLS inputs needed to reach the module conditions.
- DSC condition propagation, module-handler rollout, bundle/CSV contents and
  platform GC races remain operator-only. They are listed above rather than
  claimed by this workflow.
- The operator image build uses the Red Hat Go toolset and therefore covers the
  FIPS compile; the fast hermetic preflight deliberately does not.
- The local evidence above was gathered before the PR was opened; the
  workflow jobs are the authoritative CI proof.
