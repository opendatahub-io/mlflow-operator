# MLflow Operator Integration Tests

This directory contains the integration test image and orchestration script for the MLflow Operator.
Tests are intended to be run against a live OpenShift cluster with the MLflow Operator already installed
(via RHOAI or ODH). They are also used in CI via the test container image defined in `Dockerfile.konflux`.

The tests validate workspace-scoped RBAC behaviour by deploying a real MLflow instance via the operator,
then exercising experiment, model, and artifact operations as users with varying Kubernetes permissions.

## Prerequisites

- Logged into an OpenShift cluster (`oc whoami` should succeed)
- The MLflow Operator is already deployed (via RHOAI or ODH)
- `uv` is installed (for local runs outside the container)
- `oc` CLI is installed and on `PATH`

## Running tests locally (out-of-cluster)

On generic Kubernetes, the harness port-forwards the MLflow service to `localhost:8443` so the test
client can reach it from your machine. On OpenShift, it instead uses the MLflow CR `status.url`
gateway address by default. Set `FORCE_PORT_FORWARD=true` if you need the old localhost path on
OpenShift as well.

```bash
cd mlflow-tests

# Full run: deploys MLflow, runs tests, cleans up
bash images/test-run.sh

# Use a custom MLflow server image (recommended when testing against a specific commit)
MLFLOW_IMAGE=quay.io/opendatahub/mlflow:master bash images/test-run.sh

# Force localhost port-forwarding even on OpenShift
FORCE_PORT_FORWARD=true bash images/test-run.sh

# Skip deployment (MLflow CR already exists on the cluster)
SKIP_DEPLOYMENT=true bash images/test-run.sh

# Skip cleanup (leave the MLflow CR and role bindings in place after the run)
ARTIFACT_BACKENDS=file SKIP_CLEANUP=true bash images/test-run.sh

# Preserve the seeded deployment for later post-upgrade validation.
SKIP_CLEANUP=true \
bash images/test-run.sh -m pre_upgrade

# Reuse that preserved deployment for post-upgrade checks.
SKIP_DEPLOYMENT=true \
bash images/test-run.sh -m post_upgrade

# Also delete the reused MLflow resources after the post-upgrade run.
SKIP_DEPLOYMENT=true \
CLEANUP_REUSED_RESOURCES=true \
bash images/test-run.sh -m post_upgrade
```

If the preserved deployment includes self-deployed PostgreSQL or SeaweedFS,
`CLEANUP_REUSED_RESOURCES=true` removes those harness-managed resources too.

## Running tests in a container / CI

The test container still runs outside the MLflow pod network namespace, so connectivity follows the
same rules as other out-of-cluster runs: OpenShift uses the MLflow CR `status.url` by default,
while generic Kubernetes uses localhost port-forwarding. Use `FORCE_PORT_FORWARD=true` to force the
localhost path on OpenShift when needed.

```bash
# From the repository root
podman build -f mlflow-tests/images/Dockerfile.konflux -t mlflow-tests:latest .

# --user root is required locally because the host kubeconfig is typically chmod 600.
# This is safe with local podman; OpenShift SCCs prevent root containers in-cluster.
podman run --rm \
  --user root \
  -v $HOME/.kube:/mlflow/.kube:z \
  mlflow-tests:latest
```

The `KUBECONFIG` environment variable defaults to `/mlflow/.kube/config` in the image. If your
kubeconfig lives at a non-standard path, override it: `-v $KUBECONFIG:/mlflow/.kube/config:z`.

## Environment variables

The script is configured entirely via environment variables. Variables can also be set in
`images/.env` (sourced automatically). Run `bash images/test-run.sh --help` for the full list.

### MLflow image

| Variable | Default | Description |
|----------|---------|-------------|
| `MLFLOW_IMAGE` | _(unset)_ | Full image reference for the MLflow server. Overrides `MLFLOW_TAG` when set. |
| `MLFLOW_TAG` | `master` | Image tag appended to `MLFLOW_IMAGE_REPO`. |
| `MLFLOW_IMAGE_REPO` | `quay.io/opendatahub/mlflow` | Image repository used when `MLFLOW_IMAGE` is not set. |
| `MLFLOW_OPERATOR_IMAGE` | `quay.io/opendatahub/mlflow-operator:odh-stable` | Full image reference for the MLflow operator. |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_TYPE` | `file` | Artifact storage backend. Supported: `file`, `s3`. |
| `BACKEND_STORE` | `sqlite` | Backend store type. Supported: `sqlite`, `postgres`. |
| `REGISTRY_STORE` | `sqlite` | Registry store type. Supported: `sqlite`, `postgres`. |
| `AWS_ACCESS_KEY_ID` | _(unset)_ | S3 access key (`STORAGE_TYPE=s3` only). |
| `AWS_SECRET_ACCESS_KEY` | _(unset)_ | S3 secret key (`STORAGE_TYPE=s3` only). |
| `BUCKET` | _(unset)_ | S3 bucket name (`STORAGE_TYPE=s3` only). |
| `S3_ENDPOINT_URL` | _(unset)_ | S3 endpoint URL (`STORAGE_TYPE=s3` only). |
| `DB_HOST` | _(auto)_ | PostgreSQL hostname (when either metadata store uses `postgres`). |
| `DB_PORT` | `5432` | PostgreSQL port (when either metadata store uses `postgres`). |
| `DB_USER` | `mlflow` | PostgreSQL username. Custom values require reused/external PostgreSQL via `SKIP_INFRASTRUCTURE=true`. |
| `DB_PASSWORD` | _(unset)_ | PostgreSQL password. Custom values require reused/external PostgreSQL via `SKIP_INFRASTRUCTURE=true`. |
| `DB_NAME` | `mydatabase` | PostgreSQL database name. Custom values require reused/external PostgreSQL via `SKIP_INFRASTRUCTURE=true`. |
| `DB_SSLMODE` | _(unset)_ | SSL mode for the PostgreSQL connection URI. |

### Infrastructure image overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_IMAGE` | _(unset)_ | Override the PostgreSQL container image deployed by the script. |
| `SEAWEEDFS_IMAGE` | _(unset)_ | Override the SeaweedFS container image deployed by the script. |

### Operator / OpenShift

| Variable | Default | Description |
|----------|---------|-------------|
| `DEPLOY_MLFLOW_OPERATOR` | `false` | Set to `true` on OpenShift/OLM clusters to patch the CSV instead of deploying via kustomize. |
| `MLFLOW_OPERATOR_OWNER` | `opendatahub-io` | GitHub owner for CSV manifest download. |
| `MLFLOW_OPERATOR_REPO` | `mlflow-operator` | GitHub repo name for CSV manifest download. |
| `MLFLOW_OPERATOR_BRANCH` | `main` | Branch to pull manifests from for CSV patching. |
| `INFRASTRUCTURE_PLATFORM` | _(auto)_ | Infrastructure overlay: `base` or `openshift`. When unset, the harness inspects `route.openshift.io` and selects `openshift` only if route resources are actually present; otherwise it uses `base`. |
| `FORCE_PORT_FORWARD` | `false` | Force the harness to port-forward the MLflow service to `localhost:8443` even on OpenShift, instead of using the MLflow CR `status.url`. |

### Skip / control flags

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_DEPLOYMENT` | `false` | Skip all cluster deployment (use pre-existing resources). |
| `SKIP_OPERATOR` | `false` | Skip operator deployment only. |
| `SKIP_INFRASTRUCTURE` | `false` | Skip PostgreSQL/SeaweedFS deployment. |
| `SKIP_CLEANUP` | `false` | Leave the deployment in place after the run. Requires exactly one backend; use it for inspection or later reuse. |
| `CLEANUP_REUSED_RESOURCES` | `false` | With `SKIP_DEPLOYMENT=true`, also delete the reused MLflow CR, harness-managed RBAC, and any self-deployed PostgreSQL/SeaweedFS resources at the end of the run. |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `NAMESPACE` | `opendatahub` | Namespace where the MLflow Operator is deployed. |
| `MLFLOW_SA_NAME` | `mlflow-sa` | Service account name created by the operator. |
| `workspaces` | `workspace1-<random>,workspace2-<random>` | Comma-separated list of workspace namespaces to create and test against. |
| `upgrade_test_workspace` | `mlflow-upgrade-test-workspace` | Static workspace namespace for upgrade pytest phases and their RBAC setup. |
| `ARTIFACT_BACKENDS` | `file,s3` | Comma-separated artifact backends to run in sequence (`file`, `s3`, `externals3`). Upgrade pytest phases require exactly one value. |
| `TEST_RESULTS_DIR` | `/mlflow/results` | Directory for JUnit XML output. |
| `DEPLOY_PY` | `<repo>/.github/actions/deploy/deploy.py` | Path to the deploy helper script. |

## Storage configuration

### File storage (default)

Uses SQLite for metadata and a local PVC for artifacts. Suitable for quick local testing.

```bash
STORAGE_TYPE=file BACKEND_STORE=sqlite REGISTRY_STORE=sqlite bash images/test-run.sh
```

### S3 artifact storage

Requires `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BUCKET`, and `S3_ENDPOINT_URL` to be set.

```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BUCKET=my-bucket S3_ENDPOINT_URL=https://... \
  STORAGE_TYPE=s3 bash images/test-run.sh
```

### PostgreSQL metadata store

Set one or both metadata stores to `postgres`, then configure `DB_HOST`, `DB_PORT`, `DB_USER`,
`DB_PASSWORD`, and `DB_NAME` (via `.env` or environment variables).

```bash
BACKEND_STORE=postgres REGISTRY_STORE=postgres DB_HOST=... DB_PASSWORD=... bash images/test-run.sh
```

## Architecture notes

- **Workspace namespaces**: the test creates workspace namespaces (`workspace1`, `workspace2` by
  default) and grants the MLflow service account admin access in each. The `kubernetes-auth` backend
  embedded in the MLflow server checks RBAC in the workspace namespace on every request, so these
  role bindings are required for the tests to pass.

- **Client/server version alignment**: the test client is installed from
  `opendatahub-io/mlflow@master` (pinned in `uv.lock`). The MLflow server image must be built from
  the same commit for the workspace feature probe endpoint to match. Use `MLFLOW_IMAGE` to supply
  a freshly built image when updating the lockfile.
