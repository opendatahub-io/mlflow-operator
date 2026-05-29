# MLflow Operator

A Kubernetes operator for managing MLflow deployments on OpenShift and Kubernetes.

## Description

The MLflow Operator automates the deployment and lifecycle management of MLflow on Kubernetes and OpenShift clusters. It uses Helm charts internally to render and apply Kubernetes manifests, providing a declarative API for MLflow configuration through Custom Resources.

### Key Features

- **Declarative Configuration**: Define MLflow deployments via Kubernetes Custom Resources
- **Multi-Platform Support**: Works on both Kubernetes and OpenShift
- **Dual Deployment Modes**: Supports RHOAI and OpenDataHub deployment modes
- **Helm Chart Based**: Uses Helm charts that can be deployed standalone or via the operator
- **Environment Variable Passthrough**: Easy configuration of MLflow environment variables
- **Built-in Kubernetes Auth**: MLflow `kubernetes-auth` with `self_subject_access_review` and in-pod TLS termination
- **OpenShift Integration**: Automatic TLS certificate provisioning via service-ca-operator
- **Flexible Storage**: Support for local PVC, remote databases (PostgreSQL), and remote artifact storage (S3, etc.)
- **Persistent Storage**: Automatic PVC creation with configurable size and storage class
- **Operator-Managed Database Migrations**: The operator can scale MLflow down, run a one-shot migration Job, and restore replicas during upgrades

## Getting Started

### Prerequisites
- go version v1.24.6+
- docker version 17.03+.
- kubectl version v1.11.3+.
- Access to a Kubernetes v1.11.3+ cluster.

### Deployment Modes

The operator supports two deployment modes:

- **RHOAI Mode** (`--mode=rhoai`): Deploys to `redhat-ods-applications` namespace
- **OpenDataHub Mode** (`--mode=opendatahub`): Deploys to `opendatahub` namespace (default)

### To Deploy on the cluster

**Option 1: Using kustomize overlays (Recommended)**

For RHOAI mode:
```sh
kustomize build config/overlays/rhoai | kubectl apply -f -
```

For OpenDataHub mode:
```sh
kustomize build config/overlays/odh | kubectl apply -f -
```

**Option 2: Build and deploy from source**

Build and push your image:
```sh
make docker-build docker-push IMG=<some-registry>/mlflow-operator:tag
```

> **Building on Apple Silicon**: Use `docker-buildx` with CGO disabled to avoid QEMU emulation issues:
>
> ```sh
> CGO_ENABLED=0 PLATFORMS=linux/amd64 make docker-buildx IMG=<some-registry>/mlflow-operator:tag
> ```
>
> This builds without FIPS support, which is acceptable for local development. Production images are built on amd64 CI runners with FIPS enabled.

Install the CRDs:
```sh
make install
```

Deploy the operator:
```sh
make deploy IMG=<some-registry>/mlflow-operator:tag
```

> **NOTE**: If you encounter RBAC errors, you may need cluster-admin privileges.

**Option 3: Deploy to Open Data Hub or Red Hat OpenShift AI platform**

If Open Data Hub (ODH) or Red Hat OpenShift AI (RHOAI) is already installed on your cluster, you can use the `deploy-to-platform` target. This command automatically fetches the gateway hostname from the cluster and configures the operator to use it:

```sh
make deploy-to-platform IMG=<some-registry>/mlflow-operator:tag PLATFORM=rhoai # or PLATFORM=odh by default
```

This command:
1. Fetches the data-science-gateway hostname from the `openshift-ingress` namespace
2. Updates the `mlflow-url` in `config/base/params.env` to use `https://<gateway-hostname>`
3. Deploys the operator with the correct gateway configuration

> **IMPORTANT**: The ODH/RHOAI gateway must already exist for the HTTPRoute to work correctly. This target is only for clusters where ODH or RHOAI is already installed and the `data-science-gateway` Gateway resource is present.

You can customize the gateway name and namespace if needed:
```sh
make deploy-to-platform ODH_GATEWAY_NAME=my-gateway ODH_GATEWAY_NAMESPACE=my-namespace IMG=<some-registry>/mlflow-operator:tag
```

**Option 4: Deploy to local Kind cluster**

For local development and testing, you can deploy the MLflow operator to a Kind (Kubernetes IN Docker) cluster with various storage backend configurations:

```sh
# Deploy with default configuration (SQLite + file storage)
make deploy-kind

# Deploy with PostgreSQL backend
make deploy-kind BACKEND_STORE=postgres REGISTRY_STORE=postgres

# Deploy with S3 storage (using SeaweedFS)
make deploy-kind ARTIFACT_STORAGE=s3

# Deploy with full production-like setup
make deploy-kind BACKEND_STORE=postgres REGISTRY_STORE=postgres ARTIFACT_STORAGE=s3
```

For detailed instructions, advanced configuration options, and troubleshooting, see the [Kind Deployment Guide](docs/kind-deployment.md).

**Create MLflow instances**

> **NOTE**: The target namespace must already exist. The operator does not create namespaces.

Apply the sample MLflow CR:
```sh
kubectl apply -f config/samples/mlflow_v1_mlflow.yaml
```

The operator will automatically:
- Deploy MLflow with the specified configuration
- Set up persistent storage (PVC) if configured
- Create ServiceAccount, RBAC resources (ClusterRole, ClusterRoleBinding)
- Configure TLS certificates (OpenShift service-ca or manual)
- Run MLflow with Kubernetes auth enabled and TLS termination in-process
- Update the CR status with deployment readiness and access URLs

You can inspect the published MLflow endpoints directly from the custom resource status:

```sh
kubectl get mlflow mlflow -o jsonpath='{.status.url}{"\n"}{.status.address.url}{"\n"}'
```

- `status.url` is the external MLflow URL exposed through the data science gateway when Gateway API support is available
- `status.address.url` is the in-cluster HTTPS URL for the managed MLflow `Service`

### Standalone Helm Deployment

You can also deploy MLflow directly using Helm without the operator:

```sh
cd charts/mlflow
helm install mlflow . -n opendatahub --create-namespace
```

Customize values:
```sh
helm install mlflow . -n opendatahub --create-namespace \
  --set image.tag=v2.0.0 \
  --set storage.accessMode=ReadWriteOnce \
  --set storage.size=20Gi
```

The standalone Helm chart does not orchestrate MLflow database migrations. Bootstrap or migrate the database yourself before rolling out a standalone Helm upgrade.

## Configuration

### Authentication and Security

MLflow is deployed with the `kubernetes-auth` app enabled. The operator sets `MLFLOW_K8S_AUTH_AUTHORIZATION_MODE=self_subject_access_review`, so authorization checks are performed directly by MLflow using the caller's token. For the server itself, no special RBAC permissions are required beyond listing namespaces for the workspaces feature.

The deployment always sets `MLFLOW_DISABLE_TELEMETRY=true` and `MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false` to disable telemetry and job execution by default.

TLS is terminated inside the MLflow container using uvicorn options. Certificates come from the `mlflow-tls` secret, which is created automatically on OpenShift via the `service.beta.openshift.io/serving-cert-secret-name` annotation. If you need to provide your own certificates, place `tls.crt` and `tls.key` in a secret named `mlflow-tls` (or override `tls.secretName` in Helm values). On OpenShift, the operator sets `UVICORN_SSL_CIPHERS=PROFILE=SYSTEM` by default unless `spec.env` already defines that variable, so uvicorn follows the platform crypto policy, including FIPS-compatible TLS 1.2 and 1.3 cipher selection.

When garbage collection is enabled, the CronJob runs under a separate `mlflow-gc-sa` ServiceAccount with its own `mlflow-gc` ClusterRole and ClusterRoleBinding.

### Operator RBAC Privileges

The operator requires two levels of RBAC permissions:

- **Cluster-scoped** (`config/rbac/role.yaml`): Manages the MLflow custom resource lifecycle, enumerates namespaces, reads and watches the well-known artifact storage secret, watches MLflowConfig overrides, manages ClusterRoles/ClusterRoleBindings for MLflow server pods, and handles OpenShift console links and Gateway API routes.
- **Namespace-scoped** (`config/rbac/namespace_role.yaml`): Manages deployment resources (ConfigMaps, Secrets, ServiceAccounts, Services, PVCs, Deployments, NetworkPolicies, ServiceMonitors) within the target namespace.

The operator also creates a shared `mlflow` ClusterRole for the MLflow server pod itself, granting read-only cluster-wide access to namespaces, the well-known `mlflow-artifact-connection` secret, and MLflowConfig CRs. Secret access includes watch-based reads so namespace-specific artifact override updates can be observed across workspaces. These cannot be scoped to a single namespace because MLflow serves requests across namespaces.

See the manifest files for detailed per-resource documentation.

### Storage Configuration

`backendStoreUri` (or `backendStoreUriFrom`) is required on new creates and updates. Inline `backendStoreUri` and `registryStoreUri` intentionally accept only the documented SQL schemes (`sqlite://` and `postgresql://`). To avoid breaking already-stored CRs created before this validation was introduced, the operator still falls back to the legacy implicit SQLite backend during reconciliation when both fields are unset.

#### Local Storage (Development/Testing)
```yaml
spec:
  storage:
    accessModes:
      - ReadWriteOnce
    resources:
      requests:
        storage: 10Gi
    storageClassName: ""  # Use default storage class
  backendStoreUri: "sqlite:////mlflow/mlflow.db"
  registryStoreUri: "sqlite:////mlflow/mlflow.db"
  artifactsDestination: "file:///mlflow/artifacts"
  serveArtifacts: true
```

#### Remote Storage (Production)
```yaml
spec:
  # No storage PVC needed - using remote PostgreSQL and S3

  # Use secret references for database URIs containing credentials (recommended)
  backendStoreUriFrom:
    name: mlflow-db-credentials
    key: backend-store-uri  # postgresql://user:password@host:5432/dbname

  registryStoreUriFrom:
    name: mlflow-db-credentials
    key: registry-store-uri  # postgresql://user:password@host:5432/dbname

  artifactsDestination: "s3://my-mlflow-bucket/artifacts"
  defaultArtifactRoot: "s3://my-mlflow-bucket/artifacts/runs"
  serveArtifacts: true

  # S3 credentials via secret
  envFrom:
    - secretRef:
        name: aws-credentials  # Contains AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

  env:
    - name: AWS_DEFAULT_REGION
      value: us-east-1
```

Create the database credentials secret:
```bash
# Create secret with database URIs
kubectl create secret generic mlflow-db-credentials \
  --from-literal=backend-store-uri='postgresql://mlflow:password@postgres.example.com:5432/mlflow' \
  --from-literal=registry-store-uri='postgresql://mlflow:password@postgres.example.com:5432/mlflow' \
  -n <namespace>
```

### Database Migration

Use `spec.migration.mode` to control operator-managed database migration orchestration:

- `Automatic` (default) runs the migration Job on bootstrap and whenever `status.version` differs from the operator-supported MLflow version
- `Always` runs the migration Job for each new desired generation, meaning each new revision of the MLflow resource after its desired state changes, before the MLflow Deployment is scaled back up
- `spec.migration.ttlSecondsAfterFinished` optionally overrides how long finished migration Jobs are retained before Kubernetes TTL cleanup may delete them; when omitted, the operator defaults to 86400 seconds (24 hours), and values below 3600 seconds (1 hour) are rejected

`status.version` records the supported MLflow version that most recently completed the operator-managed migration flow. The `Migration` status condition records the per-generation migration state using `observedGeneration`: `Unknown` while migration is in progress or retrying after a transient failure, `True` after success, and `False` after a terminal failure.

Operator-managed migration only supports documented SQL metadata store URIs for the backend and registry stores: `sqlite://` and `postgresql://`. Inline `file://` backend or registry metadata URIs are intentionally rejected, and `file://` metadata stores are not supported for operator-managed migration.

If `spec.image.image` overrides the default image, the operator still uses that image for the migration Job. This supports hotfix and test images, but it also means the operator does not prevalidate the custom image's migration runtime contract before scale-down, so an incompatible custom image can still fail after the MLflow Deployment has been scaled down and cause downtime.

The operator keeps Kubernetes Job retries finite, but it automatically recreates fresh migration Jobs after a short delay for retryable failures such as transient database connectivity issues. Terminal failures, such as version mismatches, unsupported metadata store URIs, or known Alembic revision-resolution errors, stop automatic retries and instruct the admin to use `mlflow.opendatahub.io/force-migrate` after fixing the issue.

To trigger a manual one-shot rerun, add the presence-based `mlflow.opendatahub.io/force-migrate` annotation to the MLflow resource. After a successful forced migration, the operator clears the annotation automatically. If a finished Job already exists for the current desired generation, the operator deletes it first so it can create the replacement Job with the same generated name.

By default, finished migration Jobs remain visible for 24 hours before TTL cleanup may remove them. This means upgrades can leave a completed migration Job behind briefly in shared namespaces such as `redhat-ods-applications`, which gives admins time to inspect logs when needed.

During the migration flow, the operator resolves the final MLflow image, scales the MLflow Deployment to zero, waits for all MLflow replicas to disappear, runs a one-shot Job against the backend and registry stores, verifies that the migration image reports the supported MLflow version, restores the requested replica count, and updates `status.version` only after the post-migration rollout is ready.
For ODH/RHOAI MLflow images that ship `mlflow.store.db.migration_gap`, that Job also runs the backend-only RHOAI `3.3 -> 3.4` gap repair before the generic MLflow migration logic.

### CORS Configuration

The operator automatically configures `MLFLOW_SERVER_CORS_ALLOWED_ORIGINS` with safe defaults:
- Kubernetes service names (short, namespaced, and FQDN forms)
- The data science gateway domain (from the operator's `MLFLOW_URL` env var)
- `localhost` and `127.0.0.1` (for development and Kind integration tests)

To allow additional origins, use `extraAllowedOrigins` in the MLflow CR:
```yaml
spec:
  extraAllowedOrigins:
    - "https://my-app.example.com"
    - "https://jupyter.example.com:8888"
```

For standalone Helm deployments (without the operator), set `mlflow.corsAllowedOrigins` directly:
```sh
helm install mlflow . --set mlflow.corsAllowedOrigins="https://my-app.example.com,https://other.example.com"
```

### Network Security

The operator automatically creates a NetworkPolicy that:
- **Ingress**: Allows traffic to the MLflow HTTPS port (8443) from any pod in the cluster
- **Egress**: Allows DNS (ports 53 and 5353), HTTPS (ports 443, 6443, and 8443 to any destination), PostgreSQL (port 5432), MySQL (port 3306), and S3-compatible object storage (MinIO port 9000, SeaweedFS ports 8333 and 8334)

Use `networkPolicyAdditionalEgressRules` to append rules for non-default ports:
```yaml
spec:
  networkPolicyAdditionalEgressRules:
    - ports:
        - protocol: TCP
          port: 15432
```

To replace the entire default egress block (for example, to restrict HTTPS to a specific CIDR), use `networkPolicyEgressRules`. The caller is responsible for including DNS and any other required rules:
```yaml
spec:
  networkPolicyEgressRules:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
        - protocol: UDP
          port: 5353
        - protocol: TCP
          port: 5353
    - ports:
        - protocol: TCP
          port: 443
      to:
        - ipBlock:
            cidr: 10.0.0.0/8
```

### Namespace Overrides (MLflowConfig)

`MLflowConfig` is a namespaced singleton used to override artifact storage settings for a namespace.
Use `apiVersion: mlflow.kubeflow.org/v1` for `MLflowConfig` resources.
The `metadata.name` must be `mlflow` in every namespace where you want to apply overrides.
The `spec.artifactRootSecret` must be `mlflow-artifact-connection` to keep Secret access tightly scoped.
The operator still installs this CRD as part of `make install` and the kustomize overlays, but it is now kept as a vendored local copy at `config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml`, refreshed from the upstream `mlflow-kubernetes-plugins` repository.
The vendored upstream schema also validates `spec.artifactRootPath` more strictly: it must be relative, must not start with `/`, and must not contain `..` path segments.

### Custom CA Bundles

When connecting to external services that use self-signed certificates or private CAs (such as private S3 endpoints, PostgreSQL databases, or artifact stores), you can configure custom CA bundles.

The operator combines CA certificates from multiple sources into a single bundle:
1. **System CA bundle** - Base system certificates from the container image
2. **Platform CA bundle** - Automatically detected from `odh-trusted-ca-bundle` ConfigMap (injected by ODH/RHOAI)
3. **User-provided CA bundle** - Custom certificates you specify via `caBundleConfigMap`

#### Using a Custom CA Bundle

Create a ConfigMap containing your CA certificates (all `.crt` and `.pem` files will be included):
```bash
kubectl create configmap my-ca-bundle \
  --from-file=ca-bundle.crt=/path/to/your/ca-certificates.pem \
  -n <namespace>
```

Reference it in your MLflow CR:
```yaml
spec:
  caBundleConfigMap:
    name: my-ca-bundle
```

When CA bundles are present (platform or custom), PostgreSQL connections use `PGSSLMODE=verify-full`. Ensure your PostgreSQL server's certificate is signed by a CA in the bundle, or override via connection string (e.g., `?sslmode=prefer`).
### Example Configurations

See the [config/samples](./config/samples/) directory for complete examples:
- `mlflow_v1_mlflow.yaml` - OpenShift deployment with local storage and service-ca TLS
- `mlflow_v1_mlflow_remote_storage.yaml` - Remote PostgreSQL + S3 storage with horizontal scaling
- `mlflow_v1_mlflowconfig.yaml` - Namespace-scoped artifact storage override using the upstream `MLflowConfig` CRD

## Testing

MLflow coverage is split between:

- Go end-to-end tests in `test/e2e/`, including the operator-managed upgrade flow
- Python integration tests in `mlflow-tests/`

`mlflow-tests` also includes opt-in upgrade-phase pytest modules under:

- `mlflow-tests/tests/upgrade/pre_upgrade/`
- `mlflow-tests/tests/upgrade/post_upgrade/`

Versioned files such as `test_3_10.py` run only when the applicable version threshold is at least `3.10`. `pre_upgrade` gates on `MLFLOW_TEST_SUPPORTED_VERSION`; `post_upgrade` gates on the pre-upgrade version recorded in the `mlflow-upgrade-test-version` ConfigMap in `upgrade_test_workspace`.

For local runs, `bash mlflow-tests/images/test-run.sh` derives `MLFLOW_TEST_SUPPORTED_VERSION` when needed, uses `upgrade_test_workspace` as the shared namespace and RBAC target for upgrade phases, and requires exactly one artifact backend for `pre_upgrade` or `post_upgrade`. The harness auto-selects `INFRASTRUCTURE_PLATFORM=openshift` only when `route.openshift.io` resources are actually present; otherwise it uses the generic `base` overlay, and you can still override `INFRASTRUCTURE_PLATFORM` explicitly if needed. Seeded `pre_upgrade` runs against source MLflow versions before `3.12` must use tracking URIs without the `/mlflow` static prefix, while `post_upgrade` and current-version runs still use the prefixed `/mlflow` API path. A missing post-upgrade handoff ConfigMap still means there is no matching versioned dataset for that upgrade source and now exits cleanly as a successful skip, while malformed ConfigMap contents still fail fast. `.github/workflows/upgrade-validation.yml` now runs `current-upgrade-pytest-validation`, which exercises the upgrade-tagged pytest machinery itself on the current build and keeps additive datasets such as `3.11` covered, alongside `seeded-upgrade-state-validation`, which seeds a `3.10.1` deployment, patches the running operator deployment and MLflow CR to the PR-built images, and reuses that upgraded state for `post_upgrade` validation. `.github/workflows/integration-tests.yml` continues to focus on the normal current-version integration matrix.

## Shift-left Upgrade Validation

This repository keeps a repo-local operator-chaos knowledge model at `chaos/knowledge/mlflow.yaml`. The accompanying `.github/workflows/operator-chaos.yml` pull request workflow validates that knowledge file, runs `operator-chaos preflight --local`, diffs the base and PR knowledge models, compares the checked-in MLflow CRD schema with `operator-chaos diff-crds`, previews upgrade scenarios with `operator-chaos simulate-upgrade --dry-run`, and fails the PR check when the knowledge or CRD diff reports breaking changes.

This workflow is intentionally offline and asset-focused. It fails fast when validation, command execution, or breaking knowledge/CRD changes are detected, and logs the relevant operator-chaos output directly in the failing step. Update `chaos/knowledge/mlflow.yaml` whenever the stable RHOAI controller topology, default chart-managed MLflow resources, or checked-in MLflow CRD shape changes in ways that should affect upgrade modeling.

This does not replace the existing runtime upgrade coverage. Continue to use `make test-e2e-upgrade` and the `upgrade-tests` job in `.github/workflows/upgrade-validation.yml` for live migration validation.

## Troubleshooting

### Common Issues

**MLflow pods fail to start with TLS errors**:
- Verify the OpenShift service-ca operator is running and functioning
- Check if the `mlflow-tls` secret was created automatically by the service-ca operator
- Ensure the Service has the `service.beta.openshift.io/serving-cert-secret-name` annotation set

**Cannot connect to MLflow**:
- Ensure the client presents a valid Kubernetes bearer token (kubernetes-auth)
- Verify the NetworkPolicy allows traffic from your source
- Check Service and Pod status: `kubectl get svc,pods -n <namespace>`

**Storage issues**:
- Ensure the PVC is bound: `kubectl get pvc -n <namespace>`
- For remote storage, verify database/S3 credentials are correct
- Check MLflow logs (the MLflow resource name is fixed to `mlflow`):
  ```bash
  kubectl logs -n <namespace> deployment/mlflow -c mlflow
  ```

### To Uninstall
**Delete the instances (CRs) from the cluster:**

```sh
kubectl delete -k config/samples/
```

**Delete the APIs(CRDs) from the cluster:**

```sh
make uninstall
```

**UnDeploy the controller from the cluster:**

```sh
make undeploy
```
