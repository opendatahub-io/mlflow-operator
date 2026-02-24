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
IN_CLUSTER_MODE=false bash images/test-run.sh \
  --mlflow-image=quay.io/opendatahub/mlflow:master

# Skip deployment (MLflow CR already exists on the cluster)
IN_CLUSTER_MODE=false SKIP_DEPLOYMENT=true bash images/test-run.sh

# Skip cleanup (leave the MLflow CR and role bindings in place after the run)
IN_CLUSTER_MODE=false bash images/test-run.sh --skip-cleanup=true
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
  -e KUBECONFIG=/mlflow/.kube/config \
  -e IN_CLUSTER_MODE=false \
  mlflow-tests:latest
```

## CLI flags

All flags are optional. Defaults are shown.

| Flag | Default | Description |
|------|---------|-------------|
| `--mlflow-image=<image>` | `quay.io/opendatahub/mlflow:master` | Full image reference for the MLflow server to deploy. Overrides `--mlflow-tag`. |
| `--mlflow-tag=<tag>` | `master` | Image tag for `quay.io/opendatahub/mlflow`. Ignored if `--mlflow-image` is set. |
| `--storage-type=<type>` | `file` | Artifact storage backend. Supported: `file`, `s3`. |
| `--db-type=<type>` | `sqlite` | Metadata store backend. Supported: `sqlite`, `postgresql`. |
| `--deploy-mlflow-operator=<bool>` | `true` | Whether to patch the operator CSV with a custom branch before deploying. |
| `--mlflow-operator-owner=<owner>` | `opendatahub-io` | GitHub owner of the mlflow-operator repo (used for CSV patching). |
| `--mlflow-operator-repo=<repo>` | `mlflow-operator` | GitHub repo name (used for CSV patching). |
| `--mlflow-operator-branch=<branch>` | `main` | Branch to pull manifests from (used for CSV patching). |


## Environment variables

The script sources `images/.env` for defaults. All values can be overridden by setting the
corresponding environment variable before invoking the script.

| Variable | Default | Description |
|----------|---------|-------------|
| `IN_CLUSTER_MODE` | `true` | Set to `false` for local out-of-cluster runs (enables port-forwarding). |
| `NAMESPACE` | `opendatahub` | Namespace where the MLflow Operator is deployed. |
| `SKIP_DEPLOYMENT` | `true` | Skip deploying/rolling out a new MLflow Operator (use an existing one). |
| `SKIP_CLEANUP` | `false` | Skip cleaning up generated components after tests complete (MLFlow CR, RoleBinding, etc.). |
| `TEST_RESULTS_DIR` | `/tmp/test-results` | Directory for JUnit XML output. |
| `workspaces` | `workspace1-<random_hash>,workspace2-<random_hash>` | Comma-separated list of workspace namespaces to create and test against. |
| `S3_SECRET_NAME` | `mlflow-s3-secret` | Name of the Secret containing S3 credentials (S3 storage only). |
| `DB_SECRET_NAME` | `mlflow-db-secret` | Name of the Secret containing DB credentials (PostgreSQL only). |
| `DB_HOST` | `postgres.example.com` | PostgreSQL hostname (PostgreSQL only). |
| `DB_PORT` | `5432` | PostgreSQL port (PostgreSQL only). |
| `DB_USER` | `mlflow` | PostgreSQL username (PostgreSQL only). |
| `DB_PASSWORD` | `password` | PostgreSQL password (PostgreSQL only). |
| `DB_NAME` | `mlflow` | PostgreSQL database name (PostgreSQL only). |

## Storage configuration

### File storage (default)

Uses SQLite for metadata and a local PVC for artifacts. Suitable for quick local testing.

```bash
bash images/test-run.sh --storage-type=file --db-type=sqlite
```

### S3 artifact storage

Requires `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BUCKET`, and `S3_ENDPOINT_URL` to be set.

```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BUCKET=my-bucket S3_ENDPOINT_URL=https://... \
  bash images/test-run.sh --storage-type=s3
```

### PostgreSQL metadata store

**NOTE**: This hasn't been implemented for integration testing yet

Requires `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` to be configured (via `.env`
or environment variables).

```bash
bash images/test-run.sh --db-type=postgresql
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
  the same commit for the workspace feature probe endpoint to match. Use `--mlflow-image` to supply
  a freshly built image when updating the lockfile.
