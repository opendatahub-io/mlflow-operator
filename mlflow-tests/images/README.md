# MLflow Operator Integration Tests

This directory contains the integration test image and orchestration script for the MLflow Operator.
Tests are intended to be run against a live OpenShift cluster with the MLflow Operator already installed
(via RHOAI or ODH). They are also used in CI via the test container image defined in `test.Dockerfile`.

The tests validate workspace-scoped RBAC behaviour by deploying a real MLflow instance via the operator,
then exercising experiment, model, and artifact operations as users with varying Kubernetes permissions.

## Prerequisites

- Logged into an OpenShift cluster (`oc whoami` should succeed)
- The MLflow Operator is already deployed (via RHOAI or ODH)
- `uv` is installed (for local runs outside the container)
- `oc` CLI is installed and on `PATH`

## Running tests locally (out-of-cluster)

In out-of-cluster mode the script port-forwards the MLflow service to `localhost:8443` so the test
client can reach it from your machine.

```bash
cd mlflow-tests

# Full run: deploys MLflow, runs tests, cleans up
IN_CLUSTER_MODE=false bash images/test-run.sh

# Use a custom MLflow server image (recommended when testing against a specific commit)
IN_CLUSTER_MODE=false MLFLOW_IMAGE=quay.io/opendatahub/mlflow:master bash images/test-run.sh

# Skip deployment (MLflow CR already exists on the cluster)
IN_CLUSTER_MODE=false SKIP_DEPLOYMENT=true bash images/test-run.sh

# Skip cleanup (leave the MLflow CR and role bindings in place after the run)
IN_CLUSTER_MODE=false SKIP_CLEANUP=true bash images/test-run.sh
```

## Running tests in-cluster (CI / container)

When running inside the test container (as CI does), the script connects directly to the MLflow
service via its cluster-internal DNS name, bypassing the OpenShift gateway entirely.

```bash
# From the repository root
podman build -f mlflow-tests/images/test.Dockerfile -t mlflow-tests:latest .

# --user root is required locally because the host kubeconfig is typically chmod 600.
# This is safe with local podman; OpenShift SCCs prevent root containers in-cluster.
podman run --rm \
  --user root \
  -v $HOME/.kube:/mlflow/.kube:z \
  -e IN_CLUSTER_MODE=false \
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
| `DB_TYPE` | `sqlite` | Metadata store backend. Supported: `sqlite`, `postgres`. |
| `AWS_ACCESS_KEY_ID` | _(unset)_ | S3 access key (`STORAGE_TYPE=s3` only). |
| `AWS_SECRET_ACCESS_KEY` | _(unset)_ | S3 secret key (`STORAGE_TYPE=s3` only). |
| `BUCKET` | _(unset)_ | S3 bucket name (`STORAGE_TYPE=s3` only). |
| `S3_ENDPOINT_URL` | _(unset)_ | S3 endpoint URL (`STORAGE_TYPE=s3` only). |
| `DB_HOST` | _(auto)_ | PostgreSQL hostname (`DB_TYPE=postgres` only). |
| `DB_PORT` | `5432` | PostgreSQL port (`DB_TYPE=postgres` only). |
| `DB_USER` | `postgres` | PostgreSQL username (`DB_TYPE=postgres` only). |
| `DB_PASSWORD` | _(unset)_ | PostgreSQL password (`DB_TYPE=postgres` only). |
| `DB_NAME` | `mydatabase` | PostgreSQL database name (`DB_TYPE=postgres` only). |
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
| `INFRASTRUCTURE_PLATFORM` | _(auto)_ | Infrastructure overlay: `kind` or `openshift`. Auto-derived from `DEPLOY_MLFLOW_OPERATOR`. |

### Skip / control flags

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_DEPLOYMENT` | `false` | Skip all cluster deployment (use pre-existing resources). |
| `SKIP_OPERATOR` | `false` | Skip operator deployment only. |
| `SKIP_INFRASTRUCTURE` | `false` | Skip PostgreSQL/SeaweedFS deployment. |
| `SKIP_CLEANUP` | `false` | Leave resources in place after the run (useful for debugging). |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `NAMESPACE` | `opendatahub` | Namespace where the MLflow Operator is deployed. |
| `MLFLOW_SA_NAME` | `mlflow-sa` | Service account name created by the operator. |
| `IN_CLUSTER_MODE` | `true` | Set to `false` for local out-of-cluster runs (enables port-forwarding). |
| `workspaces` | `workspace1-<random>,workspace2-<random>` | Comma-separated list of workspace namespaces to create and test against. |
| `TEST_RESULTS_DIR` | `/tmp/test-results` | Directory for JUnit XML output. |
| `DEPLOY_PY` | `<repo>/.github/actions/deploy/deploy.py` | Path to the deploy helper script. |

## Storage configuration

### File storage (default)

Uses SQLite for metadata and a local PVC for artifacts. Suitable for quick local testing.

```bash
STORAGE_TYPE=file DB_TYPE=sqlite bash images/test-run.sh
```

### S3 artifact storage

Requires `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BUCKET`, and `S3_ENDPOINT_URL` to be set.

```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BUCKET=my-bucket S3_ENDPOINT_URL=https://... \
  STORAGE_TYPE=s3 bash images/test-run.sh
```

### PostgreSQL metadata store

**NOTE**: This hasn't been implemented for integration testing yet.

Requires `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` to be configured (via `.env`
or environment variables).

```bash
DB_TYPE=postgres DB_HOST=... DB_PASSWORD=... bash images/test-run.sh
```

## Architecture notes

- **Workspace namespaces**: the test creates workspace namespaces (`workspace1`, `workspace2` by
  default) and grants the MLflow service account admin access in each. The `kubernetes-auth` backend
  embedded in the MLflow server checks RBAC in the workspace namespace on every request, so these
  role bindings are required for the tests to pass.

- **Tracking URI vs. static prefix**: the MLflow server is deployed with `--static-prefix=/mlflow`,
  which prefixes UI, health, and ajax-api routes. The Python client uses REST API routes
  (`/api/2.0/mlflow/...`) which are **not** affected by the static prefix. `MLFLOW_TRACKING_URI`
  must therefore point at the service root without `/mlflow`.

- **Client/server version alignment**: the test client is installed from
  `opendatahub-io/mlflow@master` (pinned in `uv.lock`). The MLflow server image must be built from
  the same commit for the workspace feature probe endpoint to match. Use `MLFLOW_IMAGE` to supply
  a freshly built image when updating the lockfile.
