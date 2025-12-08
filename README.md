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
- **Authentication Proxy**: Optional kube-rbac-proxy sidecar for Kubernetes RBAC-based authentication
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

Install the CRDs:
```sh
make install
```

Deploy the operator:
```sh
make deploy IMG=<some-registry>/mlflow-operator:tag
```

> **NOTE**: If you encounter RBAC errors, you may need cluster-admin privileges.

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
- Deploy kube-rbac-proxy sidecar if enabled
- Configure TLS certificates (OpenShift service-ca or manual)
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
  --set storage.size=20Gi \
  --set kubeRbacProxy.enabled=true \
  --set openShift.servingCert.enabled=false
```

## Configuration

### Authentication and Security

#### kube-rbac-proxy (Platform-Agnostic)

The operator supports deploying a kube-rbac-proxy sidecar for Kubernetes RBAC-based authentication. This feature works on both OpenShift and vanilla Kubernetes.

```yaml
spec:
  kubeRbacProxy:
    enabled: true
    image:
      image: "quay.io/opendatahub/odh-kube-auth-proxy:latest"
      pullPolicy: IfNotPresent
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
    tls:
      secretName: mlflow-tls  # Secret with tls.crt and tls.key
      upstreamCAFile: /etc/tls/upstream-ca/ca.crt  # Path to upstream CA cert
      upstreamCASecret: mlflow-upstream-ca  # Optional: Secret containing upstream CA
```

#### TLS Certificate Configuration

**OpenShift (Automatic)**:
```yaml
spec:
  openShift:
    servingCert:
      enabled: true
      secretName: mlflow-tls  # OpenShift will provision cert here
```

**Vanilla Kubernetes (Manual)**:
```yaml
spec:
  kubeRbacProxy:
    enabled: true
    tls:
      secretName: mlflow-tls-manual
      upstreamCAFile: /etc/tls/upstream-ca/ca.crt
      upstreamCASecret: mlflow-upstream-ca
```

Then create the required secrets:
```bash
# Server TLS certificate (for kube-rbac-proxy)
kubectl create secret tls mlflow-tls-manual \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key \
  -n <namespace>

# Upstream CA certificate (for kube-rbac-proxy to validate MLflow)
kubectl create secret generic mlflow-upstream-ca \
  --from-file=ca.crt=path/to/ca.crt \
  -n <namespace>
```

### Storage Configuration

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
  # No storage PVC needed
  backendStoreUri: "postgresql://mlflow:password@postgres.example.com:5432/mlflow"
  registryStoreUri: "postgresql://mlflow:password@postgres.example.com:5432/mlflow"
  artifactsDestination: "s3://my-mlflow-bucket/artifacts"

  # S3 credentials
  envFrom:
    - secretRef:
        name: aws-credentials  # Contains AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

  env:
    - name: AWS_DEFAULT_REGION
      value: us-east-1
```

### Network Security

The operator automatically creates a NetworkPolicy that:
- **Ingress**: Allows traffic to port 8443 (kube-rbac-proxy) when enabled, or port 9443 (direct MLflow) when disabled
- **Egress**: Allows all outbound traffic (for database connections, S3 access, etc.)

The NetworkPolicy can be customized by modifying the Helm chart values or by creating your own NetworkPolicy.

### Example Configurations

See the [config/samples](./config/samples/) directory for complete examples:
- `mlflow_v1_mlflow.yaml` - OpenShift deployment with local storage and kube-rbac-proxy
- `mlflow_v1_mlflow_remote_storage.yaml` - Remote PostgreSQL + S3 storage with horizontal scaling
- `mlflow_v1_mlflow_manual_tls.yaml` - Vanilla Kubernetes with manual TLS certificates

## Troubleshooting

### Common Issues

**MLflow pods fail to start with TLS errors**:
- Verify the TLS secret exists and contains `tls.crt` and `tls.key`
- On OpenShift, ensure `openShift.servingCert.enabled: true` is set
- On vanilla Kubernetes, ensure you've created the TLS secret manually

**kube-rbac-proxy fails with upstream CA errors**:
- On OpenShift: The default CA file path should work automatically
- On vanilla Kubernetes: Ensure you've set `upstreamCASecret` and created the corresponding secret with `ca.crt`

**Cannot connect to MLflow**:
- Check if kube-rbac-proxy is enabled - if so, you need to authenticate via Kubernetes RBAC
- Verify the NetworkPolicy allows traffic from your source
- Check Service and Pod status: `kubectl get svc,pods -n <namespace>`

**Storage issues**:
- Ensure the PVC is bound: `kubectl get pvc -n <namespace>`
- For remote storage, verify database/S3 credentials are correct
- Check MLflow logs: `kubectl logs -n <namespace> deployment/mlflow -c mlflow`

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