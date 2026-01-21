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
- Update the CR status with deployment readiness

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
  --set storage.size=20Gi
```

## Configuration

### Authentication and Security

MLflow is deployed with the `kubernetes-auth` app enabled. The operator sets `MLFLOW_K8S_AUTH_AUTHORIZATION_MODE=self_subject_access_review`, so authorization checks are performed directly by MLflow using the caller's token—no special RBAC permissions are required beyond listing namespaces for the workspaces feature.

The deployment always sets `MLFLOW_DISABLE_TELEMETRY=true` and `MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false` to disable telemetry and job execution by default.

TLS is terminated inside the MLflow container using uvicorn options. Certificates come from the `mlflow-tls` secret, which is created automatically on OpenShift via the `service.beta.openshift.io/serving-cert-secret-name` annotation. If you need to provide your own certificates, place `tls.crt` and `tls.key` in a secret named `mlflow-tls` (or override `tls.secretName` in Helm values).

### Storage Configuration

`backendStoreUri` (or `backendStoreUriFrom`) is required and must be set explicitly. Avoid relying on implicit defaults to prevent data loss with ephemeral storage.

#### Local Storage (Development/Testing)
```yaml
spec:
  storage:
    size: 10Gi
    storageClassName: ""  # Use default storage class
    accessMode: ReadWriteOnce

  backendStoreUri: "sqlite:////mlflow/mlflow.db"
  registryStoreUri: "sqlite:////mlflow/mlflow.db"
  artifactsDestination: "file:///mlflow/artifacts"
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

### Network Security

The operator automatically creates a NetworkPolicy that:
- **Ingress**: Allows traffic to the MLflow HTTPS port (8443)
- **Egress**: Allows all outbound traffic (for database connections, S3 access, etc.)

The NetworkPolicy can be customized by modifying the Helm chart values or by creating your own NetworkPolicy.

### Namespace Overrides (MLflowConfig)

`MLflowConfig` is a namespaced singleton used to override artifact storage settings for a namespace.
Use `apiVersion: mlflow.kubeflow.org/v1` for `MLflowConfig` resources.
The `metadata.name` must be `mlflow` in every namespace where you want to apply overrides.
The `spec.artifactRootSecret` must be `mlflow-artifact-connection` to keep Secret access tightly scoped.

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
- `mlflow_v1_mlflowconfig.yaml` - Namespace-scoped artifact storage override

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
- Check MLflow logs:
  ```bash
  # For CR named "mlflow":
  kubectl logs -n <namespace> deployment/mlflow -c mlflow

  # For CR with custom name (e.g., "production"):
  kubectl logs -n <namespace> deployment/mlflow-production -c mlflow
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
