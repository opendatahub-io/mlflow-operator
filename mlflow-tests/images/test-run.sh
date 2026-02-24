#!/bin/bash

set -o allexport
source ./images/.env
source ./images/patch-csv.sh
set +o allexport

RANDOM_SUFFIX=$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)
# TODO(gfrasca): MLFlow Operator only monitors one predetermined namespace, can't use a random namespace.
# export NAMESPACE="${NAMESPACE:-${NAMESPACE}-$RANDOM_SUFFIX}"
export NAMESPACE="${NAMESPACE:-opendatahub}"

export MLFLOW_NAME="mlflow"
export MLFLOW_SA_NAME="mlflow-sa-$MLFLOW_NAME"
export DEPLOYMENT_NAME="mlflow"

export API_BASE="https://${MLFLOW_NAME}.${NAMESPACE}.svc.cluster.local:8443"
export API_HEALTH_URL="${API_BASE}/mlflow/health"

SKIP_DEPLOYMENT="${SKIP_DEPLOYMENT:-true}"
SKIP_CLEANUP="${SKIP_CLEANUP:-false}"
IN_CLUSTER_MODE="${IN_CLUSTER_MODE:-true}"

MLFLOW_TAG="master"
MLFLOW_IMAGE_REPO="quay.io/opendatahub/mlflow"
MLFLOW_IMAGE=""  # If set, overrides MLFLOW_IMAGE_REPO:MLFLOW_TAG entirely
DEPLOY_MLFLOW_OPERATOR="true"
STORAGE_TYPE="file"
DB_TYPE="sqlite"

# CSV configuration
MLFLOW_OPERATOR_OWNER="opendatahub-io"
MLFLOW_OPERATOR_REPO="mlflow-operator"
MLFLOW_OPERATOR_BRANCH="main"  # TODO(gfrasca): Make this should be 'stable' once branch exists

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mlflow-tag=*)
      MLFLOW_TAG="${1#*=}"
      shift
      ;;
    --mlflow-image=*)
      MLFLOW_IMAGE="${1#*=}"
      shift
      ;;
    --mlflow-operator-owner=*)
      MLFLOW_OPERATOR_OWNER="${1#*=}"
      shift
      ;;
    --mlflow-operator-repo=*)
      MLFLOW_OPERATOR_REPO="${1#*=}"
      shift
      ;;
    --mlflow-operator-branch=*)
      MLFLOW_OPERATOR_BRANCH="${1#*=}"
      shift
      ;;
    --deploy-mlflow-operator=*)
      DEPLOY_MLFLOW_OPERATOR="${1#*=}"
      shift
      ;;
    --storage-type=*)
      STORAGE_TYPE="${1#*=}"
      shift
      ;;
    --db-type=*)
      DB_TYPE="${1#*=}"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

#################### GENERATE CONFIG FILES #######################

# Track resources created by this script so cleanup only removes what it made.
# Pre-existing cluster resources are never touched.
_CREATED_S3_SECRET=false
_CREATED_DB_SECRET=false
_CREATED_WORKSPACES=""  # comma-separated list of workspace namespaces created here

# Resolve the final MLflow image to deploy
MLFLOW_RESOLVED_IMAGE="${MLFLOW_IMAGE:-${MLFLOW_IMAGE_REPO}:${MLFLOW_TAG}}"
echo "Using MLflow image: $MLFLOW_RESOLVED_IMAGE"

# 1. Create a temporary files to store mlflow config
mlflow_deployment=$(mktemp)
postgres_deployment=$(mktemp)  # TODO(gfrasca): Create postgres_deployment file
mlflow_role_binding=$(mktemp)

# Create MLflow CR template
cat <<EOF >> "$mlflow_deployment"
apiVersion: mlflow.opendatahub.io/v1
kind: MLflow
metadata:
  name: $MLFLOW_NAME
  namespace: $NAMESPACE
spec:

  image: 
    image: "$MLFLOW_RESOLVED_IMAGE"
    imagePullPolicy: Always
  replicas: 1
  resources:
    limits:
      cpu: 500m
      memory: 512Mi
    requests:
      cpu: 100m
      memory: 256Mi
  workers: 1
  serviceAccountName: $MLFLOW_SA_NAME
EOF

# Add storage-specific configuration
if [ "$STORAGE_TYPE" == "s3" ]; then
  # Create S3 credentials secret
  echo "Creating AWS credentials secret for S3 storage"
  if oc -n "$NAMESPACE" create secret generic "$S3_SECRET_NAME" \
    --from-literal=AWS_ACCESS_KEY="$AWS_ACCESS_KEY_ID" \
    --from-literal=AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"; then
    _CREATED_S3_SECRET=true
  fi

  # Add S3 storage configuration to MLflow CR
  cat <<EOF >> "$mlflow_deployment"
  artifactsDestination: s3://$BUCKET/mlflow/artifacts
  envFrom:
    - secretRef:
        name: $S3_SECRET_NAME
  env:
    - name: MLFLOW_S3_ENDPOINT_URL
      value: $S3_ENDPOINT_URL
EOF
else
  cat <<EOF >> "$mlflow_deployment"
  serveArtifacts: true
  artifactsDestination: file:///mlflow/artifacts
EOF
fi

if [ "$DB_TYPE" == "postgresql" ]; then
  echo "Creating PostgreSQL credentials secret"
  if oc -n "$NAMESPACE" create secret generic "$DB_SECRET_NAME" \
    --from-literal=backend-store-uri="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" \
    --from-literal=registry-store-uri="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"; then
    _CREATED_DB_SECRET=true
  fi

  cat <<EOF >> "$mlflow_deployment"
  backendStoreUriFrom:
    name: $DB_SECRET_NAME
    key: backend-store-uri
  registryStoreUriFrom:
    name: $DB_SECRET_NAME
    key: registry-store-uri
EOF
else
  cat <<EOF >> "$mlflow_deployment"
  backendStoreUri: "sqlite:////mlflow/mlflow.db"
  registryStoreUri: sqlite:////mlflow/mlflow.db
EOF
fi

# If file-based storage, create a PVC
if [ "$DB_TYPE" == "sqlite" ] || [ "$STORAGE_TYPE" == "file" ]; then
  cat <<EOF >> "$mlflow_deployment"
  storage:
    accessModes:
      - ReadWriteOnce
    resources:
      requests:
        storage: 1Gi
EOF
fi

# Admin role binding in the MLflow namespace
cat <<EOF >> "$mlflow_role_binding"
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: mlflow-permissions-$MLFLOW_NAME
  namespace: $NAMESPACE
subjects:
  - kind: ServiceAccount
    name: $MLFLOW_SA_NAME
    namespace: $NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: admin
EOF

# The kubernetes-auth backend checks RBAC in the workspace (namespace) on every request.
# Grant the admin SA access in each workspace namespace too.
WORKSPACE_LIST="${workspaces:-workspace1-${RANDOM_SUFFIX},workspace2-${RANDOM_SUFFIX}}"
for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
  ws=$(echo "$ws" | xargs)
  [ -z "$ws" ] && continue
  # Track only namespaces this script creates; pre-existing ones are left alone at cleanup.
  if ! oc get namespace "$ws" &>/dev/null; then
    _CREATED_WORKSPACES="${_CREATED_WORKSPACES:+${_CREATED_WORKSPACES},}${ws}"
  fi
  cat <<EOF >> "$mlflow_role_binding"
---
apiVersion: v1
kind: Namespace
metadata:
  name: $ws
---
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: mlflow-permissions-$MLFLOW_NAME
  namespace: $ws
subjects:
  - kind: ServiceAccount
    name: $MLFLOW_SA_NAME
    namespace: $NAMESPACE
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: admin
EOF
done

#################### PATCH CSV #######################
# Patch the CSV to mount custom mlflow-operator manifests (from a GitHub branch)
# into the operator pod via a PVC
if [ "$DEPLOY_MLFLOW_OPERATOR" == "true" ] && [ "$SKIP_DEPLOYMENT" == "false" ]; then
  if ! find_csv_and_update "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH"; then
    echo "Error: Failed to patch CSV"
    exit 1
  fi
  echo "CSV patched successfully"
fi

#################### DEPLOY MLFLOW #######################

# Define cleanup function
cleanup() {
  echo "Cleaning up resources..."

  # Clean up port-forward if used (Local mode)
  if [ -n "$PF_PID" ] && kill -0 "$PF_PID" 2>/dev/null; then
    echo "Stopping port-forward (pid $PF_PID)"
    kill "$PF_PID"
  fi

  # TODO(gfrasca): deleting the namespace is too destructive, commented out for now until we
  # can create resources in a separate namespace.
  # if [ -n "$NAMESPACE" ] && oc get namespace "$NAMESPACE" &> /dev/null; then
  #   echo "Deleting namespace: $NAMESPACE"
  #   oc delete namespace "$NAMESPACE"
  # fi

  # Clean up workspace resources. The RoleBinding is always script-created so always
  # remove it. The namespace is only deleted if this script created it.
  for ws in $(echo "$WORKSPACE_LIST" | tr ',' ' '); do
    ws=$(echo "$ws" | xargs)
    [ -z "$ws" ] && continue
    if oc get rolebinding "mlflow-permissions-$MLFLOW_NAME" -n "$ws" &> /dev/null; then
      echo "Deleting role binding: mlflow-permissions-$MLFLOW_NAME in $ws"
      oc delete rolebinding "mlflow-permissions-$MLFLOW_NAME" -n "$ws"
    fi
  done
  for ws in $(echo "$_CREATED_WORKSPACES" | tr ',' ' '); do
    ws=$(echo "$ws" | xargs)
    [ -z "$ws" ] && continue
    if oc get namespace "$ws" &> /dev/null; then
      echo "Deleting namespace: $ws"
      oc delete namespace "$ws"
    fi
  done

  # Clean up k8s resources individually than to delete the namespace
  if [ -n "$MLFLOW_NAME" ]; then
    if oc get mlflow "$MLFLOW_NAME" -n "$NAMESPACE" &> /dev/null; then
      echo "Deleting mlflow CR: $MLFLOW_NAME"
      oc delete mlflow "$MLFLOW_NAME" -n "$NAMESPACE"
    fi
    if oc get rolebinding "mlflow-permissions-$MLFLOW_NAME" -n "$NAMESPACE" &> /dev/null; then
      echo "Deleting role binding: mlflow-permissions-$MLFLOW_NAME"
      oc delete rolebinding "mlflow-permissions-$MLFLOW_NAME" -n "$NAMESPACE"
    fi
  fi

  if [ -n "$POSTGRES_NAME" ] && oc get deployment "$POSTGRES_NAME" -n "$NAMESPACE" &> /dev/null; then
    echo "Deleting deployment: $POSTGRES_NAME"
    oc delete deployment "$POSTGRES_NAME" -n "$NAMESPACE"
  fi

  # Only delete secrets this script created; pre-existing secrets are left alone.
  if [ "$_CREATED_S3_SECRET" == "true" ] && oc get secret "$S3_SECRET_NAME" -n "$NAMESPACE" &> /dev/null; then
    echo "Deleting secret: $S3_SECRET_NAME"
    oc delete secret "$S3_SECRET_NAME" -n "$NAMESPACE"
  fi

  if [ "$_CREATED_DB_SECRET" == "true" ] && oc get secret "$DB_SECRET_NAME" -n "$NAMESPACE" &> /dev/null; then
    echo "Deleting secret: $DB_SECRET_NAME"
    oc delete secret "$DB_SECRET_NAME" -n "$NAMESPACE"
  fi

  # Clean up temporary files
  if [ -n "$mlflow_deployment" ] && [ -f "$mlflow_deployment" ]; then
    rm -f "$mlflow_deployment"
  fi
  if [ -n "$mlflow_role_binding" ] && [ -f "$mlflow_role_binding" ]; then
    rm -f "$mlflow_role_binding"
  fi

  if [ -n "$postgres_deployment" ] && [ -f "$postgres_deployment" ]; then
    rm -f "$postgres_deployment"
  fi
}

# Set trap to run cleanup on script exit (success or failure)
if [ ! "$SKIP_CLEANUP" == "true" ]; then
  trap cleanup EXIT
fi

# Create namespace if it doesn't already exist
oc create namespace "$NAMESPACE" 2>/dev/null || true

if [ "$DB_TYPE" == "postgresql" ]; then
  # Deploy PostgreSQL database
  echo "Deploying PostgreSQL database"
  oc apply -n "$NAMESPACE" -f "$postgres_deployment"
fi

# Create MLflow deployment
echo "Creating MLflow deployment"
cat "$mlflow_deployment"
oc apply -n "$NAMESPACE" -f "$mlflow_deployment"

# Apply RBAC role bindings (MLflow namespace + workspace namespaces)
echo "Applying MLflow role bindings"
cat "$mlflow_role_binding"
oc apply -f "$mlflow_role_binding"
timeout 1m bash -c \
  "until oc -n $NAMESPACE get deployment $DEPLOYMENT_NAME &> /dev/null; do echo 'Waiting for the deployment $DEPLOYMENT_NAME...'; sleep 10; done"
if oc wait --for=condition=available deployment/$MLFLOW_NAME --timeout=10m -n "$NAMESPACE"; then
  echo "MLFlow pod is ready"
else
  echo "Warning: MLflow pod did not become ready within timeout, continuing anyway..."
  exit 1
fi

# Determine the MLflow tracking URI based on deployment context.
# NOTE: The tracking URI must NOT include the /mlflow static prefix because the Python client
# uses REST API routes (/api/2.0/mlflow/...) which are not prefixed by --static-prefix.
# The /mlflow prefix is only applied to UI/health/ajax-api routes by Flask.
if [ "$IN_CLUSTER_MODE" == "true" ]; then
  # Inside the cluster: use the service cluster DNS directly — bypasses the gateway entirely.
  export MLFLOW_TRACKING_URI="$API_BASE"
  HEALTH_URL="$API_HEALTH_URL"
else
  # Outside the cluster: port-forward to the MLflow service for local debugging.
  # The gateway hostname (*.apps.*) resolves via DNS to the OpenShift HAProxy router, not
  # the Istio gateway, so external HTTPS through the gateway hostname does not work from a
  # developer machine without extra DNS or /etc/hosts configuration.
  echo "Port-forwarding MLflow service to localhost:8443 for out-of-cluster access..."
  oc port-forward "svc/${MLFLOW_NAME}" -n "$NAMESPACE" 8443:8443 &
  PF_PID=$!
  sleep 2
  export MLFLOW_TRACKING_URI="https://localhost:8443"
  HEALTH_URL="https://localhost:8443/mlflow/health"
fi
echo "Using MLFLOW_TRACKING_URI: $MLFLOW_TRACKING_URI"

# Wait for the MLflow health endpoint to respond.
echo "Waiting for MLflow endpoint to become reachable at $HEALTH_URL ..."
RETRY=0
MAX_RETRIES=36  # 36 * 5s = 3 minutes
until curl -sk --max-time 5 -o /dev/null -w "%{http_code}" "$HEALTH_URL" | grep -qE "^(200|302)$"; do
  RETRY=$((RETRY + 1))
  if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
    echo "Error: MLflow endpoint did not become reachable within timeout"
    exit 1
  fi
  echo "  Attempt $RETRY/$MAX_RETRIES: endpoint not ready yet, retrying in 5s..."
  sleep 5
done
echo "MLflow endpoint is reachable"

#################### TESTS #######################
# Get API Token
echo "Generate Token for $MLFLOW_SA_NAME"
if ! kube_token=$(oc create token "$MLFLOW_SA_NAME" --namespace "$NAMESPACE" --duration=60m); then
  echo "Error: Failed to create token for $MLFLOW_SA_NAME"
  exit 1
fi
export kube_token

# Create test results directory
mkdir -p "$TEST_RESULTS_DIR"

# Execute Pytest and capture its exit code explicitly so the script propagates
# test failures to the CI job rather than always reporting success.
echo "Running Tests now..."
uv run pytest \
  --junit-xml="$TEST_RESULTS_DIR/test-results.xml"
TEST_EXIT_CODE=$?

# Report results
echo "Test execution completed. Results in: $TEST_RESULTS_DIR"
if ls "$TEST_RESULTS_DIR"/*.xml 1> /dev/null 2>&1; then
    echo "JUnit XML reports generated successfully"
else
    echo "Warning: No XML reports found"
fi

exit $TEST_EXIT_CODE
