# Deploying MLflow Operator to Local Kind Environment

This document provides a comprehensive guide for deploying the MLflow Operator and MLflow instances to a local Kind (Kubernetes IN Docker) cluster.

## Prerequisites

Before you begin, ensure you have the following tools installed:

- [Docker](https://docs.docker.com/get-docker/) (for running Kind)
- [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) (Kubernetes IN Docker)
- [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl/) (Kubernetes CLI)
- [kustomize](https://kubectl.docs.kubernetes.io/installation/kustomize/) (for building Kubernetes manifests)
- [envsubst](https://www.gnu.org/software/gettext/manual/html_node/envsubst-Invocation.html) (usually available in gettext package)
- Python 3.x (for running the deployment script)

## Quick Start

### 1. Create a Kind Cluster

```bash
kind create cluster --name mlflow-cluster
```

### 2. Deploy with Default Configuration (SQLite + File Storage)

```bash
make deploy-kind
```

This will deploy MLflow with:
- SQLite backend store
- SQLite registry store
- File-based artifact storage

### 3. Access MLflow UI

```bash
kubectl port-forward service/mlflow 8080:5000 -n opendatahub
```

Then open your browser to <http://localhost:8080>

## Configuration Options

The deployment supports various storage backend configurations:

### Storage Backend Options

| Backend Store | Registry Store | Artifact Storage | Description |
|---------------|----------------|------------------|-------------|
| sqlite | sqlite | file | Default, simplest setup |
| postgres | postgres | file | PostgreSQL for metadata, file for artifacts |
| sqlite | sqlite | s3 | SQLite for metadata, S3-compatible for artifacts |
| postgres | postgres | s3 | Full production-like setup with PostgreSQL and S3 |

## Deployment Examples

### Example 1: Default Configuration (SQLite + File)

```bash
# Using make target
make deploy-kind

# Or using the deployment script directly
./.github/actions/deploy/deploy.py
```

### Example 2: PostgreSQL Backend with File Storage

```bash
make deploy-kind BACKEND_STORE=postgres REGISTRY_STORE=postgres
```

### Example 3: Full S3 Setup with SeaweedFS

```bash
make deploy-kind ARTIFACT_STORAGE=s3
```

### Example 4: Full Production-like Setup

```bash
make deploy-kind BACKEND_STORE=postgres REGISTRY_STORE=postgres ARTIFACT_STORAGE=s3
```

## Advanced Configuration

### Using the Python Deployment Script Directly

The deployment script `./.github/actions/deploy/deploy.py` provides more granular control:

```bash
# Basic deployment with custom namespace
./.github/actions/deploy/deploy.py --namespace my-mlflow

# PostgreSQL backend with custom credentials
./.github/actions/deploy/deploy.py \
    --backend-store postgres \
    --registry-store postgres \
    --postgres-user myuser \
    --postgres-password mypassword

# S3 storage with custom bucket and credentials
./.github/actions/deploy/deploy.py \
    --artifact-storage s3 \
    --s3-bucket my-bucket \
    --s3-access-key my-access-key \
    --s3-secret-key my-secret-key

# Custom MLflow and operator images
./.github/actions/deploy/deploy.py \
    --mlflow-image my-registry/mlflow:custom-tag \
    --mlflow-operator-image my-registry/mlflow-operator:custom-tag

# Deploy everything with custom configuration
./.github/actions/deploy/deploy.py \
    --namespace production-mlflow \
    --backend-store postgres \
    --registry-store postgres \
    --artifact-storage s3 \
    --postgres-user produser \
    --postgres-password prodpassword \
    --s3-bucket prod-artifacts \
    --s3-access-key prod-access-key \
    --s3-secret-key prod-secret-key
```

### Script Parameters

The deployment script accepts the following parameters:

#### Basic Configuration
- `--namespace`: Kubernetes namespace (default: `opendatahub`)
- `--mlflow-image`: MLflow container image (default: `quay.io/opendatahub/mlflow:master`)
- `--mlflow-operator-image`: MLflow operator image (default: `quay.io/opendatahub/mlflow-operator:main`)

#### Storage Configuration
- `--backend-store`: Backend store type [`sqlite`, `postgres`] (default: `sqlite`)
- `--registry-store`: Registry store type [`sqlite`, `postgres`] (default: `sqlite`)
- `--artifact-storage`: Artifact storage type [`file`, `s3`] (default: `file`)
- `--serve-artifacts`: Whether to serve artifacts [`true`, `false`] (default: `true`)

#### Custom URIs (for advanced users)
- `--backend-store-uri`: Custom backend store URI (default: `sqlite:////mlflow/mlflow.db`)
- `--registry-store-uri`: Custom registry store URI (default: `sqlite:////mlflow/mlflow.db`)
- `--artifacts-destination`: Custom artifacts destination (default: `file:///mlflow/artifacts`)

#### PostgreSQL Configuration
- `--postgres-host`: PostgreSQL host (auto-configured for in-cluster deployment)
- `--postgres-port`: PostgreSQL port (default: `5432`)
- `--postgres-user`: PostgreSQL username (default: `postgres`)
- `--postgres-password`: PostgreSQL password (default: `mysecretpassword`)
- `--postgres-backend-db`: Backend database name (default: `mydatabase`)
- `--postgres-registry-db`: Registry database name (default: `mydatabase`)

#### S3 Configuration
- `--s3-bucket`: S3 bucket name (default: `mlpipeline`)
- `--s3-access-key`: S3 access key (default: `minio`)
- `--s3-secret-key`: S3 secret key (default: `minio123`)
- `--s3-endpoint`: S3 endpoint URL (auto-configured for in-cluster SeaweedFS)

## What Gets Deployed

### Default Components (All Configurations)
- MLflow Operator (manages MLflow instances)
- MLflow Custom Resource (defines the MLflow deployment)
- TLS certificates (for webhook communication)
- RBAC resources (service accounts, roles, bindings)

### Additional Components (Based on Configuration)

#### When using PostgreSQL (`--backend-store postgres` or `--registry-store postgres`)
- PostgreSQL deployment with persistent storage
- PostgreSQL service for database access
- Database credentials secret

#### When using S3 storage (`--artifact-storage s3`)
- SeaweedFS deployment (S3-compatible object storage)
- MinIO service endpoint for S3 API
- S3 credentials secret
- Initialization job to configure S3 buckets

## Troubleshooting

### Common Issues

1. **Port 8080 already in use**
   ```bash
   # Use a different port for port-forward
   kubectl port-forward service/mlflow 8081:5000 -n opendatahub
   ```

2. **SeaweedFS initialization fails**
   ```bash
   # Check SeaweedFS logs
   kubectl logs deployment/seaweedfs -n opendatahub

   # Check initialization job
   kubectl logs job/init-seaweedfs -n opendatahub
   ```

3. **PostgreSQL connection issues**
   ```bash
   # Check PostgreSQL logs
   kubectl logs deployment/postgres-deployment -n opendatahub

   # Verify database credentials
   kubectl get secret mlflow-db-credentials -n opendatahub -o yaml
   ```

4. **MLflow operator not starting**
   ```bash
   # Check operator logs
   kubectl logs deployment/mlflow-operator-controller-manager -n opendatahub

   # Verify TLS certificates
   kubectl get secret mlflow-webhook-server-cert -n opendatahub
   ```

### Debugging Commands

```bash
# Check all resources in the namespace
kubectl get all -n opendatahub

# Check MLflow Custom Resource status
kubectl describe mlflow mlflow -n opendatahub

# View operator logs
kubectl logs deployment/mlflow-operator-controller-manager -n opendatahub

# Check MLflow logs
kubectl logs deployment/mlflow -n opendatahub

# Verify storage (if using PostgreSQL)
kubectl exec -it deployment/postgres-deployment -n opendatahub -- psql -U postgres -l

# Test S3 connectivity (if using S3)
kubectl exec -it deployment/seaweedfs -n opendatahub -- weed shell
```

## Cleanup

### Remove MLflow Deployment

```bash
make undeploy-kind
```

### Remove Kind Cluster

```bash
kind delete cluster --name mlflow-cluster
```

## Directory Structure

The Kind deployment manifests are organized as follows:

```
config/overlays/kind/
├── kustomization.yaml              # Main overlay configuration
├── params.env                     # Environment variables
├── generate-tls.sh                 # TLS certificate generation script
├── manager-patch.yaml              # Operator manager patches
├── manager-rolebinding-patch.yaml  # RBAC patches
├── metrics-auth-rolebinding-patch.yaml
├── mlflow-tls.yaml                 # TLS secret template
├── postgres/                       # PostgreSQL manifests
│   ├── deployment.yaml
│   ├── pvc.yaml
│   ├── secret.yaml
│   └── service.yaml
└── seaweedfs/                      # SeaweedFS (S3) manifests
    └── base/
        ├── kustomization.yaml
        └── seaweedfs/
            ├── seaweedfs-deployment.yaml
            ├── seaweedfs-service.yaml
            ├── seaweedfs-pvc.yaml
            ├── minio-service.yaml
            └── ...
```

The deployment script is located at:
```
.github/actions/deploy/
├── action.yml                      # GitHub Action definition
└── deploy.py                       # Python deployment script
```

## Contributing

When modifying the Kind deployment configuration:

1. Test your changes with different storage combinations
2. Update this documentation with any new parameters or features
3. Ensure the make targets work correctly
4. Test cleanup procedures

For more information about the MLflow Operator itself, see the main [README.md](../README.md).