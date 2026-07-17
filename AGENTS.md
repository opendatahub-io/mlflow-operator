# MLflow Operator

Kubernetes operator for managing MLflow deployments.

## Project Structure

This project was generated using [Kubebuilder](https://book.kubebuilder.io/) v4.10.1.

## Resources

### MLflow (mlflow.opendatahub.io/v1)

The MLflow custom resource is **cluster-scoped**, meaning it can be created without specifying a namespace and is accessible across the entire cluster.

### MLflowOperator (components.platform.opendatahub.io/v1alpha1)

The `MLflowOperator` custom resource is a **cluster-scoped singleton module CR** named `default-mlflowoperator`. It exists to carry platform-level MLflow module state and status during the ODH modularization handoff. The new in-repo `MLflowOperator` controller path is guarded by the `ENABLE_MLFLOW_OPERATOR_MODULE_CONTROLLER` rollout toggle and should stay disabled by default until the coordinating ODH module-handler change is ready. When that toggle is enabled, startup waits up to `MLFLOW_OPERATOR_MODULE_CONTROLLER_CRD_WAIT_TIMEOUT` for the `MLflowOperator` CRD and fails startup if the timeout expires, so explicit enablement never silently skips controller registration. Its status condition item schema intentionally mirrors the ODH platform operator condition shape, including optional `severity` and `lastHeartbeatTime`, so the shared handoff CR stays wire-compatible while both sides are rolling out.
`APPLICATIONS_NAMESPACE` remains a Deployment/startup input for the operator itself and is not projected through the `MLflowOperator` CR spec, so cache scope and namespace-scoped RBAC stay aligned with the live operand target namespace.

### MLflowConfig (mlflow.kubeflow.org/v1)

The MLflowConfig custom resource is **namespace-scoped**, allowing Kubernetes namespace owners to override the default artifact storage configuration for their namespace.
For security, `spec.artifactRootSecret` is fixed to `mlflow-artifact-connection` via CEL validation.
Its CRD is vendored in this repo at `config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml` and should be refreshed from the `mlflow-kubernetes-plugins` repository when the upstream schema changes.
The vendored upstream schema also requires `spec.artifactRootPath` to be relative and forbids `..` path segments.

### Namespace RBAC Controller

The namespace RBAC controller is a **dedicated reconciliation loop** that watches cluster-wide Namespace objects labeled `opendatahub.io/global-mlflow-workspace: <MLFLOW_CR_NAME>` and reconciles `odh-group-mlflow-view` and `odh-group-mlflow-edit` RoleBindings in each. Subjects are read from the platform Auth CR (`services.platform.opendatahub.io/v1alpha1`) via unstructured client to avoid importing the ODH platform API module. The controller path is guarded by the `ENABLE_NAMESPACE_RBAC` rollout toggle (default `false`). When enabled, startup checks for the Auth CRD via the discovery API and fails if it is absent, so explicit enablement never silently skips controller registration. RoleBindings are owned by the MLflow CR via `controllerutil.SetControllerReference`, giving Kubernetes GC a safety net when the operator is offline. RoleBinding watches use dedicated per-name caches (`ViewRBWatchCache`, `EditRBWatchCache`) with `metadata.name` field selectors because the operator's RBAC uses `resourceNames`-scoped permissions — a general informer cache without a field selector would be rejected by the API server. The Auth CR watch uses a separate `AuthWatchCache` following the same `GCRBACWatchCache` pattern established for the `mlflow-gc` RBAC objects.

## API Definitions

The `api/` folder contains the API type definitions owned by this repository:

```text
api/
├── mlflowoperator/
│   └── v1alpha1/
│       ├── groupversion_info.go     # MLflowOperator module API registration
│       ├── mlflowoperator_types.go  # MLflowOperator singleton module CR
│       └── zz_generated.deepcopy.go # Auto-generated DeepCopy methods
├── v1/
│   ├── groupversion_info.go     # MLflow API group and version registration
│   ├── mlflow_types.go          # MLflow resource type definitions
│   └── zz_generated.deepcopy.go # Auto-generated DeepCopy methods
```

### Modifying API Types

To add or modify fields in the MLflow resource:

1. Edit the relevant API type file:
   - Add fields to `MLflowSpec` for desired state
   - Add fields to `MLflowStatus` for observed state
   - Use Kubebuilder markers for validation, defaults, and CRD generation

2. Regenerate code and manifests:
   ```bash
   make manifests generate
   ```
   The Makefile intentionally scopes `controller-gen` to the root controller package and the nested `api/` module so nested repo copies or temp trees inside the workspace do not pollute generated output. Keep the Kubernetes dependency versions in the root module and `api/go.mod` aligned so generation runs against the same API types the operator binary uses.

3. The CRDs will be updated at:
   - `config/crd/bases/mlflow.opendatahub.io_mlflows.yaml`
   - `config/crd/bases/components.platform.opendatahub.io_mlflowoperators.yaml`

Changes to the `MLflowConfig` schema must be made in the upstream `mlflow-kubernetes-plugins` repository, then vendored here at `config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml`. This repo references that local copy from `config/crd/kustomization.yaml`.

**Important**: Never manually edit `zz_generated.deepcopy.go` - it's automatically generated by `make generate`.

> Note: json tags are required.  Any new fields you add must have json tags for the fields to be serialized.

## Development

Generated code and manifests are managed through Kubebuilder's code generation tools:

```bash
# Generate CRD manifests, RBAC, and webhooks
make manifests

# Generate Go code (DeepCopy, DeepCopyInto, DeepCopyObject)
make generate
```

**Note**: Always regenerate manifests and code after modifying API types. CI will verify that generated code is up-to-date.

The operator now fails fast at startup when `MLFLOW_IMAGE` is empty. Checked-in manifests still set that environment variable for normal overlay-based installs, and `deploy.py` requires `--mlflow-image` when it is creating an `MLflow` custom resource.
During the MLflow modularization transition, keep legacy ODH deployment behavior and the new module-controller path cleanly separated. Prefer isolated helpers for legacy-vs-modular branching, and add concise inline comments only at the toggle boundary or where fallback precedence is otherwise non-obvious.
Keep shipped install manifests under `config/`. CI/local-test-only kustomize trees such as Kind/dev overlays and self-managed PostgreSQL/SeaweedFS scaffolding now live under `.github/test-infra/` so downstream manifest consumers like `opendatahub-operator` only vendor production assets from `config/`.

When bumping the supported MLflow version, update `config/component_metadata.yaml`, then rerun the version-alignment verification. The Makefile injects `SupportedMLflowVersion` from that metadata via Go `-ldflags`; the test harness and test image read the same value through `scripts/print_supported_mlflow_version.py`. Top-level `scripts/` is preferred for developer-maintenance helpers like this; `test/scripts/` is reserved for test validation helpers.

## Deployment Modes

The MLflow operator supports two deployment modes, configured via the `--mode` flag:

### RHOAI Mode
```bash
--mode=rhoai
```
- Deploys MLflow resources to the `redhat-ods-applications` namespace
- Use the RHOAI overlay: `config/overlays/rhoai/`

### OpenDataHub Mode (default)
```bash
--mode=opendatahub
```
- Deploys MLflow resources to the `opendatahub` namespace
- Use the OpenDataHub overlay: `config/overlays/odh/`

### Deploying with Different Modes

Using kustomize overlays:

```bash
# Deploy with RHOAI mode
kustomize build config/overlays/rhoai | kubectl apply -f -

# Deploy with OpenDataHub mode
kustomize build config/overlays/odh | kubectl apply -f -
```

## Helm Chart

The operator uses Helm charts to manage MLflow resources. The chart is located in `charts/mlflow/` and can be used standalone or via the operator.
Standalone Helm deployments must not orchestrate database migrations; migration orchestration is operator-only.

## Helm Chart and MLflowSpec parity

The Helm chart’s values must maintain parity with the configuration parameters exposed through the MLflowSpec. While the MLflowSpec may introduce additional configuration fields, this should occur only in exceptional and well-justified cases.

As a general principle, configuration options that are specific to OpenDataHub or Red Hat OpenShift AI deployments may remain exclusively within the MLflowSpec and do not need to be represented in the Helm chart values.
Conversely, any configuration that is relevant to users deploying the Helm charts independently of the operator—on generic Kubernetes or OpenShift environments—must be configurable in both the Helm chart values and the MLflowSpec.
### Standalone Deployment

You can deploy MLflow directly using Helm without the operator:

```bash
cd charts/mlflow
helm install mlflow . -n opendatahub --create-namespace
```

### Chart Structure

```
charts/mlflow/
├── Chart.yaml                    # Chart metadata
├── values.yaml                   # Default configuration values
└── templates/                    # Kubernetes manifests
    ├── namespace.yaml
    ├── serviceaccount.yaml
    ├── rbac.yaml
    ├── pvc.yaml
    ├── deployment.yaml           # MLflow server with kubernetes-auth and TLS
    ├── cronjob.yaml              # Garbage collection CronJob
    └── service.yaml
```

### OpenShift Integration

When deploying on OpenShift (`openShift.enabled: true`), the deployment includes:

- **In-pod TLS**: Uvicorn terminates TLS using the service-ca-generated secret
- **Service CA**: Automatically provisions TLS certificates
- **FIPS compatibility**: On OpenShift, the operator sets `UVICORN_SSL_CIPHERS=PROFILE=SYSTEM` by default so uvicorn follows the platform crypto policy and continues to accept both TLS 1.2 and TLS 1.3; if the MLflow CR sets `UVICORN_SSL_CIPHERS` in `spec.env`, the operator preserves that value

The Helm chart does not create an OpenShift Route. Create your own Route if you need external exposure on OpenShift.

### Customizing Values

The MLflow CR spec fields map directly to Helm chart values. See example configurations:
- `config/samples/mlflow_v1_mlflow.yaml` - Local storage (SQLite + file-based artifacts) with a commented DRA example
- `config/samples/mlflow_v1_mlflow_remote_storage.yaml` - PostgreSQL primary/read-replica routing + S3

### Storage Configuration

MLflow has three independent storage components:

1. **Backend Store** (experiment metadata): Inline `backendStoreUri` supports `sqlite://` and `postgresql://`
2. **Registry Store** (model registry metadata): Inline `registryStoreUri` supports the same SQL schemes as `backendStoreUri`
3. **Artifacts Destination** (artifacts storage): Supports `file://`, `s3://`, `gs://`, `wasbs://`, `hdfs://`

The `storage` field in the MLflow CR is **optional** and only needed for file-based storage:

**When to configure storage:**
- Using `sqlite://` for backend/registry store
- Using `file://` for artifacts destination
- Development/testing with local storage

**When storage is NOT needed:**
- Using remote database (PostgreSQL, MySQL)
- Using remote object storage (S3, GCS, Azure Blob)
- Production deployments (recommended)

**Examples:**

Local storage (requires PVC):
```yaml
spec:
  storage:
    accessModes:
      - ReadWriteOnce
    resources:
      requests:
        storage: 10Gi
  backendStoreUri: "sqlite:////mlflow/mlflow.db"
  registryStoreUri: "sqlite:////mlflow/mlflow.db"
  artifactsDestination: "file:///mlflow/artifacts"
  serveArtifacts: true
```

Remote storage (no PVC):
```yaml
spec:
  # No storage field needed
  backendStoreUri: "postgresql://user:pass@host:5432/mlflow"
  artifactsDestination: "s3://my-bucket/artifacts"
  defaultArtifactRoot: "s3://my-bucket/artifacts/runs"
  serveArtifacts: true
  envFrom:
    - secretRef:
        name: aws-credentials
```

Optional read-replica routing is configured with exactly one of `readReplicaBackendStoreUri` or `readReplicaBackendStoreUriFrom`. The replica must already have a compatible schema. One replica URI is used for both tracking and model-registry reads, so deployments with separate tracking and registry databases should configure it only when that endpoint can correctly serve both stores.

### Operator-managed database migration

- `spec.migration.mode` controls operator-managed migration behavior:
  - `Automatic` (default) runs the migration Job on bootstrap and whenever `status.version` differs from the operator-supported MLflow version
  - `Always` reruns the migration flow for each new desired generation, meaning each new revision of the MLflow resource after its desired state changes, before the MLflow Deployment is scaled back up
- `spec.migration.ttlSecondsAfterFinished` optionally overrides how long finished operator-managed migration Jobs are retained before Kubernetes TTL cleanup may remove them; when omitted, the operator defaults to 86400 seconds (24 hours), and values below 3600 seconds (1 hour) are rejected
- Finished migration Jobs can therefore remain visible for up to 24 hours in shared namespaces such as `redhat-ods-applications`, which is intentional so admins have time to inspect logs after upgrades
- `status.version` records the last supported MLflow version that successfully completed the operator-managed migration/deploy flow
- The `Migration` status condition records the per-generation migration state via `observedGeneration`: `Unknown` while migration is in progress or retrying after a transient failure, `True` after success, and `False` after a terminal failure
- Operator-managed migration only supports documented SQL metadata store URIs (`sqlite://`, `postgresql://`) for backend and registry stores; inline `file://` metadata URIs are intentionally rejected and are not supported for operator-managed migration
- If `spec.image.image` overrides the default image, the operator still uses that image for the migration Job to support hotfix and test images, but it does not prevalidate the custom image's migration runtime contract before scale-down; an incompatible custom image can therefore fail after the MLflow Deployment has been scaled down and cause downtime
- The operator-managed migration Job verifies that the resolved MLflow image reports `SupportedMLflowVersion` before it advances `status.version`
- Kubernetes Job retries remain finite, but the operator recreates fresh migration Jobs after a short delay for retryable failures such as transient connectivity problems; terminal failures such as version mismatches, unsupported metadata store URIs, or known Alembic revision-resolution errors stop automatic retries
- For ODH/RHOAI MLflow images that ship `mlflow.store.db.migration_gap`, the operator-managed migration Job runs the backend-only RHOAI `3.3 -> 3.4` gap repair before the generic MLflow migration logic; this replaced the earlier Deployment init-container approach
- The presence-based `mlflow.opendatahub.io/force-migrate` annotation forces a one-shot migration; the operator clears it after a successful forced run, and if a finished Job already exists for the current desired generation, the operator deletes it first so it can create the replacement Job with the same generated name. Terminal migration failures instruct admins to use that annotation after fixing the issue.
- When backend and registry store URIs differ, the migration Job must handle them independently and only advance `status.version` after both succeed
- Migration Jobs must explicitly neutralize `MLFLOW_READ_REPLICA_BACKEND_STORE_URI`; schema initialization and upgrades always target the primary backend and registry stores

## Testing

### Unit Tests

Unit tests are located in `internal/controller/` and can be run with:

```bash
make test
```

### E2E Tests

End-to-end tests are located in `test/e2e/` and require a Kind cluster:

```bash
make test-e2e-full
```

`make test-e2e` expects an already-running Kubernetes cluster and does not create one.
`make test-e2e-full` creates a Kind cluster (`KIND_CLUSTER`, default `mlflow`), builds/loads the image, and runs e2e tests.
`make test-e2e-upgrade` runs the upgrade-focused e2e suite against an existing cluster and expects `MLFLOW_SEED_IMAGE` to point at a known-good MLflow `3.10.1` seed image plus `MLFLOW_RUNTIME_IMAGE` to point at the current target MLflow image. The seed default is pinned to the ODH release 1.1 digest (`v3.10.1+rhaiv.3`) so the upgrade path does not depend on rebuilding an intermediate seed image during test setup. The GitHub workflow deploys a `3.10.1`-compatible operator image plus a running MLflow `3.10.1` instance, and the upgrade Ginkgo test then scales the operator down, repins the MLflow CR to the current runtime image, switches the operator Deployment to the current image, and scales the operator back up before verifying the operator-managed migration flow.
Cluster cleanup is a separate step:

```bash
make cleanup-kind-cluster
```

Quick workflow:

```bash
# Full e2e run against Kind
make test-e2e-full

# Upgrade-focused e2e run against an existing cluster
make test-e2e-upgrade \
  MLFLOW_SEED_IMAGE=quay.io/opendatahub/mlflow@sha256:ad51bbd7f770491da88dc1db3b3c84f7471d25c48026ecb385180b63b18f4c64 \
  MLFLOW_RUNTIME_IMAGE=localhost/mlflow-runtime:ci

# Cleanup when done
make cleanup-kind-cluster
```

### MLflow upgrade pytest phases

The `mlflow-tests/` suite also contains opt-in, version-gated upgrade pytest coverage under:

- `mlflow-tests/tests/upgrade/pre_upgrade/`
- `mlflow-tests/tests/upgrade/post_upgrade/`

Versioned files are named by the minimum supported MLflow major/minor version they apply to. For example, `test_3_10.py` runs only when the relevant pre-upgrade version is at least `3.10`. Ignore build suffixes such as `+rhaiv.3`; always normalize to `x.y`.

Selection rules:

- `pre_upgrade` files compare their filename threshold against `MLFLOW_TEST_SUPPORTED_VERSION`
- `post_upgrade` files compare their filename threshold against the pre-upgrade version stored in the namespace-scoped ConfigMap `mlflow-upgrade-test-version`
- The ConfigMap lives in the same static upgrade workspace namespace used by the seeded MLflow resources so pre/post phases share one namespace-scoped handoff point
- A missing post-upgrade handoff ConfigMap is treated as "no matching versioned dataset for this source version" and should exit cleanly as a successful skip; malformed or empty ConfigMap contents must still fail the run instead of being treated as an empty selection
- Harness-driven upgrade phases use the static `upgrade_test_workspace` namespace as the source of truth for both pytest setup and shell-harness RBAC/workspace creation
- Seeded `pre_upgrade` runs against source MLflow versions before `3.12` must use tracking URIs without the `/mlflow` static prefix; `post_upgrade` and current-version runs still use the prefixed `/mlflow` API path
- When `INFRASTRUCTURE_PLATFORM` is unset, `mlflow-tests/images/test-run.sh` must treat OpenShift as present only if `kubectl api-resources --api-group=route.openshift.io -o name` returns at least one resource; checking the command exit code alone misdetects Kind as OpenShift and breaks the upstream `postgres:13` overlay selection
- On OpenShift, `mlflow-tests/images/test-run.sh` now uses the MLflow CR `status.url` gateway address by default, but `FORCE_PORT_FORWARD=true` forces the legacy localhost port-forward path when a run must bypass gateway routing
- Normal current-version multi-backend `mlflow-tests/images/test-run.sh` runs must tear down the `MLflow` CR plus any self-managed PostgreSQL / SeaweedFS infrastructure between backend suites so the next suite does not inherit metadata or object-storage state from the prior one
- Preserve the upgrade tracking-URI prefix logic on every connectivity path: seeded source versions before `3.12` still need a prefixless tracking URI even on the OpenShift `status.url` branch
- Harness-driven upgrade phases require exactly one artifact backend; do not rely on the default multi-backend `file,s3` loop for `-m pre_upgrade` or `-m post_upgrade`
- Harness-driven preserve/reuse flows also require exactly one artifact backend when `SKIP_CLEANUP=true`; reject ambiguous multi-backend preserve requests instead of preserving only the last backend
- Harness-driven `SKIP_DEPLOYMENT=true` reuse runs preserve the reused `MLflow` CR and RBAC by default; set `CLEANUP_REUSED_RESOURCES=true` to always tear them down after validation, or `CLEANUP_REUSED_RESOURCES=on_success` to preserve failed runs for debugging while still cleaning up successful runs when `SKIP_CLEANUP=false`
- Local Fedora/SELinux validation of the containerized `mlflow-tests` harness can fail before pytest starts if the test image (runs as UID `1001`) cannot read `~/.kube/config` or write the mounted results directory. Treat these as host-environment blockers, not suite failures: when reproducing the workflow locally, prefer running the container as the host user (for example `--user "$(id -u):$(id -g)"`) and keep SELinux-friendly bind labels such as `:z` on shared mounts or `:Z` on private writable temp/result directories.

These upgrade pytest suites are opt-in only. A plain `pytest -v` run should not execute them unless the pytest marker expression explicitly selects `pre_upgrade` or `post_upgrade`.

The long-form testing map for the RHOAI MLflow fork now lives in
`docs/rhoai-mlflow-testing.md`. Keep that guide aligned whenever Jenkins
shift-left MLflow entrypoints, workflow names in this repository, or the MLflow
fork's migration-gap and UI-E2E references change.

### Linting

This repo uses golangci-lint; to ensure linting is successful after code changes, run

```bash
golangci-lint run
```

### README.md

Whenever making any changes, always ensure the content in the README.md at the root of the repo is up to date.

### Sample CRs (config/samples/)

The `config/samples/` directory contains example MLflow custom resource configurations that demonstrate different deployment scenarios. These samples serve as:
- Reference documentation for users
- Test cases for validation
- Examples in the README

**Available samples:**

1. **mlflow_v1_mlflow.yaml** - Default configuration
   - OpenShift deployment with service-ca certificates
   - Local storage (SQLite + file-based artifacts)
   - TLS terminated by MLflow (uvicorn) using service-ca certs
   - Includes a commented `spec.resourceClaims` plus `spec.resources.claims` Dynamic Resource Allocation example

2. **mlflow_v1_mlflow_minimal.yaml** - Minimal configuration
   - Local storage with minimal resources
   - Suitable for development/testing

3. **mlflow_v1_mlflow_manual_tls.yaml** - Manual TLS configuration
   - Vanilla Kubernetes deployment
   - Manual TLS certificate management
   - Shows upstreamCASecret configuration

4. **mlflow_v1_mlflow_remote_storage.yaml** - Remote storage with garbage collection
   - PostgreSQL primary/read-replica routing for metadata
   - S3 for artifacts
   - No PVC required (fully remote)
   - Multi-replica deployment
   - Periodic garbage collection via CronJob
   - `mlflow-gc-sa` ServiceAccount for the CronJob
   - Suffixed `mlflow-gc{{ resourceSuffix }}` ClusterRole and ClusterRoleBinding for the CronJob

5. **mlflow_v1_mlflow_digest.yaml** - Digest-based images
   - Uses SHA256 image digests for reproducibility
   - Shows MLflow image by digest
   - Includes instructions for obtaining digests

6. **mlflow_v1_mlflow_trace_archival.yaml** - Trace archival configuration
   - PostgreSQL for metadata, S3 for artifacts and trace archive
   - CronJob runs the standalone trace archival module on a schedule
   - Archival config mounted into the MLflow server for UI awareness
   - Configures archival location, retention, schedule, and max traces per pass

7. **mlflow_v1_mlflowconfig.yaml** - Namespace artifact storage override
   - Override artifact storage with custom bucket and path
   - Example of namespace-specific artifact configuration
   - Requires Secret with S3 credentials in the same namespace

**When to update samples:**

Samples MUST be updated when:
- API fields are added, removed, or renamed in `api/v1/mlflow_types.go`
- The upstream `MLflowConfig` CRD changes in a way that affects `config/samples/mlflow_v1_mlflowconfig.yaml`
- Default values change in the controller or Helm chart
- New configuration features are added
- Configuration semantics change

**How to validate samples:**

After updating samples, ensure:
1. All samples are valid against the CRD:
   ```bash
   make manifests  # Generate CRDs first

   # Validate locally (client-side only, no cluster required)
   # Find all MLflow sample files by content, not naming pattern
   mapfile -t MLFLOW_SAMPLES < <(
     grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null | grep -v mlflowconfig || true
   )
   for sample in "${MLFLOW_SAMPLES[@]}"; do
     echo "Validating $sample..."
     cat config/crd/bases/mlflow.opendatahub.io_mlflows.yaml "$sample" | \
       kubectl apply --dry-run=client -f -
   done

   # Validate MLflowConfig samples
   # Note: Validate these against a cluster after installing the vendored CRD:
   #   kubectl apply -f config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml
   mapfile -t MLFLOWCONFIG_SAMPLES < <(
     grep -l "kind: MLflowConfig" config/samples/*.yaml 2>/dev/null || true
   )
   for sample in "${MLFLOWCONFIG_SAMPLES[@]}"; do
     echo "Validating $sample..."
     kubectl apply --dry-run=server -f "$sample"
   done

   # CI automatically validates samples on every PR with full schema validation
   # (CI installs CRDs into a Kind cluster before validation)
   ```

2. Helm chart can render all configurations:
   ```bash
   # Test each sample's configuration maps to valid Helm values
   make test
   ```

3. Examples in README.md reference the correct sample files

> **Note**: `kubectl apply --dry-run=client` performs client-side validation only and does NOT connect to any cluster or deploy anything. It's safe to use for local development.

**Automated validation:**

The `.github/workflows/validate-samples.yaml` workflow automatically validates samples on every PR:
- Creates a Kind cluster for validation
- Installs the CRD into the cluster
- Validates all samples against the CRD schema using `kubectl apply --dry-run=server` (full server-side validation)
- Verifies samples are documented in AGENTS.md
- Verifies samples are referenced in kustomization.yaml
- Runs on changes to samples, API, or CRD definitions

**Sample maintenance checklist:**
- [ ] All samples use current API version and fields
- [ ] Comments accurately describe what each field does
- [ ] Deprecated fields are removed or marked as deprecated
- [ ] New features have at least one example in samples
- [ ] kustomization.yaml lists all available samples
- [ ] Each sample has a clear use case and description

## CI/CD Workflows

The repository uses GitHub Actions for continuous integration. Key workflows:

### `.github/workflows/validate-samples.yaml`
Validates sample CRs on every PR:
- **Purpose**: Ensures samples remain valid as API evolves
- **Validations**:
  - CRD schema compliance using kubeconform
  - All samples documented in AGENTS.md
  - All samples referenced in kustomization.yaml
- **Triggers**: Changes to samples, API, CRD, or workflows
- **Prevents**: Merging invalid or undocumented samples

### Other Workflows
- `verify-codegen.yml` - Validates generated code is up-to-date
- `test.yml` - Runs unit tests
- `lint.yml` - Runs golangci-lint
- `build-and-push-test-image.yml` - Publishes the public `quay.io/opendatahub/mlflow-tests` image for `main`, `release-*`, and `rhoai-*` branches as a multi-arch manifest covering `amd64` and `arm64`; keep `mlflow-tests/images/Dockerfile.konflux` downloads architecture-aware (`TARGETARCH`) whenever adding new CLI tooling
- `integration-tests.yml` - Normal MLflow runtime workflow for push and pull request validation. It builds one operator image artifact from this repository, one MLflow runtime image artifact from the matching owner MLflow repository, and one `mlflow-tests` image artifact from this repository, then reuses those artifacts in the current-version integration matrix by executing the test image as the harness entrypoint. The one intentional exception is ODH default-branch parity: operator `main` maps to `opendatahub-io/mlflow@master`. The operator image build uses `Dockerfile.konflux` when that file exists in the checked-out branch and falls back to `Dockerfile` otherwise. Built-image version alignment remains RHDS-only, and ODH push/PR activity targeting `main` still verifies the default image version from the checked-out repo without depending on the runtime-image build job. The workflow also carries a Jenkins-like multi-backend matrix row that runs `file,s3` in a single `test-run.sh` invocation so between-suite teardown regressions are exercised in CI.
- `upgrade-validation.yml` - Upgrade-focused MLflow workflow for push and pull request validation. It builds and reuses the same operator, runtime, and test image artifacts for two complementary marker-validation jobs plus the upgrade-path e2e job. `current-upgrade-pytest-validation` runs `mlflow-tests` `pre_upgrade` / `post_upgrade` directly on the PR-built images so the upgrade-tagged pytest machinery itself and additive datasets such as `3.11` stay exercised. `seeded-upgrade-state-validation` seeds a `3.10.1`-compatible source deployment, patches the running operator Deployment and seeded MLflow CR in place to the PR-built images, and then runs the `mlflow-tests` `post_upgrade` suite against that upgraded state across both file and S3 artifact backends. The `build-mlflow-tests-image` job also verifies that the direct-execution test image exposes the repo's normalized supported MLflow version through `MLFLOW_TEST_SUPPORTED_VERSION`, matching the Jenkins-style container entry path. Upgrade tests use a pinned ODH release 1.1 seed image digest (`v3.10.1+rhaiv.3`) instead of rebuilding `test/e2e/images/mlflow-3.10.0.Dockerfile`, which was removed after its `microdnf`-based Python reinstall started pulling an incompatible sqlite runtime; the workflow retags that digest to a local seed tag before `kind load` and deployment so containerd does not have to resolve the digest-only reference during container creation. Failed jobs upload debug artifacts with namespace snapshots plus operator, MLflow, and migration-job pod logs and descriptions when available.
- `operator-chaos.yml` - Offline operator-chaos upgrade-risk gate. It validates `chaos/knowledge/mlflow.yaml`, runs `operator-chaos preflight --local`, diffs the base and PR knowledge models with `operator-chaos diff --breaking`, compares the checked-in MLflow CRD schema with `operator-chaos diff-crds`, previews generated upgrade experiments with `operator-chaos simulate-upgrade --dry-run`, and fails fast when validation, command execution, or breaking knowledge/CRD changes are reported. This workflow is intentionally asset-focused and does not create a cluster or execute live chaos experiments.
- `verify-mlflow-version-alignment.yml` - Scheduled ODH-only default-image alignment check. It runs directly from the checked-out operator repo and verifies the `config/base/params.env` plus `.github/test-infra/overlays/kind/params.env` defaults against the supported MLflow version metadata without instantiating the heavier integration workflow.
- `test-e2e.yml` - Runs end-to-end tests
- `verify-kustomize.yml` - Validates kustomize overlays

**When modifying workflows:**
- Follow existing patterns and naming conventions
- Always pin external GitHub Actions or reusable workflow references to immutable commit SHAs rather than floating tags or branches; resolve the current SHA programmatically when updating them
- Update AGENTS.md if adding new validation requirements
- Test workflow changes in a fork before merging

## Agent notes

Any agent working with this repo should always ensure:
1. **AGENTS.md** is kept up to date with any changes made to this repo. Only add changes that will help future agents, take care not to add unnecessary noise.
2. **config/samples/** directory contains up-to-date example CRs that reflect the current API structure
3. **README.md** references are consistent with actual sample files
4. **GitHub workflows** will automatically validate samples—ensure changes pass CI checks
5. **Code Comments** do not make self-evident code comments, especially when the information is plainly obvious looking at the code
6. **`chaos/knowledge/mlflow.yaml`** stays aligned with the stable RHOAI operator and default MLflow steady-state topology; update it whenever controller-managed resources, the checked-in MLflow CRD, or the default chart/RBAC shape changes in ways the operator-chaos workflow should model
