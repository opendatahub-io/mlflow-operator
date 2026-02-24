#!/bin/bash

set -o allexport
source images/.env
set +o allexport

# Function to patch CSV
patch_csv() {
    local CSV_NAME="$1"
    local NAMESPACE_NAME="$2"
    local MLFLOW_OPERATOR_OWNER="$3"
    local MLFLOW_OPERATOR_REPO="$4"
    local MLFLOW_OPERATOR_BRANCH="$5"

    echo "Starting mlflow-operator component patching for CSV: $CSV_NAME in namespace: $NAMESPACE_NAME"

    # Get the directory where this script is located
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local overlays_dir="$script_dir/overlays"

    # Step 1: Create PersistentVolumeClaim
    echo "Creating PVC for mlflow-operator component manifests..."
    if oc apply -f "$overlays_dir/mlflow-operator-pvc.yaml" -n "$NAMESPACE_NAME"; then
        echo "PVC created successfully"
    else
        echo "Failed to create PVC, continuing anyway..."
    fi

    # Step 2: Download and build MLflow operator manifest
    local base_config_path
    base_config_path=$(download_and_prepare_mlflow_operator_manifests "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH")
    if [[ $? -ne 0 || -z "$base_config_path" ]]; then
        echo "ERROR: Failed to download and prepare MLflow operator manifests"
        exit 1
    fi

    # Step 3: Apply CSV patch and wait for operator pod readiness
    local operator_label
    operator_label=$(apply_csv_patch_and_wait_for_mlflow_operator "$CSV_NAME" "$NAMESPACE_NAME" "$overlays_dir")
    if [[ $? -ne 0 || -z "$operator_label" ]]; then
        echo "ERROR: Failed to apply CSV patch or wait for MLflow operator pod readiness"
        exit 1
    fi

    # Step 4: Copy MLflow operator manifests to the operator pods
    copy_mlflow_operator_manifests_to_pods "$base_config_path" "$operator_label" "$NAMESPACE_NAME"
    local copy_result=$?
    if [[ $copy_result -eq 1 ]]; then
        echo "ERROR: Failed to copy custom manifests to any pods"
        exit 1
    fi

    # Step 5: Restart operator deployment
    echo "Restarting MLflow operator deployment to pick up changes..."
    if oc rollout restart deploy -n "$NAMESPACE_NAME" -l "$operator_label"; then
        echo "Operator deployment restart initiated"

        # Wait for rollout to complete
        echo "Waiting for deployment rollout to complete..."
        if oc rollout status deploy -n "$NAMESPACE_NAME" -l "$operator_label" --timeout=300s; then
            echo "Operator deployment rollout completed successfully"
        else
            echo "Warning: Deployment rollout did not complete within timeout"
            exit 1
        fi
    else
        echo "Failed to restart operator deployment"
        return 1
    fi

    # Step 6: Wait for MLflow operator controller manager pod to be running
    wait_for_mlflow_operator_controller_manager "$NAMESPACE_NAME"

    echo "Finished patching mlflow-operator component for CSV: $CSV_NAME"
}

# Function to apply CSV patch and wait for operator pod readiness
# Returns the operator label via stdout on success
apply_csv_patch_and_wait_for_mlflow_operator() {
    local csv_name="$1"
    local namespace_name="$2"
    local overlays_dir="$3"

    echo "Checking if mlflow-operator volume mount already exists..." >&2

    # Check if the volume mount already exists
    if oc get csv "$csv_name" -n "$namespace_name" -o jsonpath='{.spec.install.spec.deployments[0].spec.template.spec.containers[0].volumeMounts[*].name}' | grep -q "mlflow-operator-manifests"; then
        echo "mlflow-operator volume mount already exists, skipping patch..." >&2
    else
        echo "Applying CSV patch to mount mlflow-operator manifests volume..." >&2
        if oc patch csv "$csv_name" -n "$namespace_name" --type json --patch-file "$overlays_dir/mlflow-operator-csv-patch.json"; then
            echo "CSV patch applied successfully" >&2
        else
            echo "Failed to apply CSV patch, exiting..." >&2
            return 1
        fi
    fi

    echo "Waiting for operator pod to be ready..." >&2
    local operator_label=""
    if [[ "$namespace_name" == "redhat-ods-operator" ]]; then
        operator_label="name=rhods-operator"
    elif [[ "$namespace_name" == "openshift-operators" ]]; then
        operator_label="name=opendatahub-operator"
    else
        echo "Unknown namespace: $namespace_name, using generic operator label" >&2
        operator_label="app=operator"
    fi

    # Wait up to 5 minutes for pod to be ready
    if oc wait --for='jsonpath={.status.conditions[?(@.type=="Ready")].status}=True' po -l "$operator_label" -n "$namespace_name" --timeout=300s >&2; then
        echo "Operator pod is ready" >&2
    else
        echo "Warning: Operator pod did not become ready within timeout, continuing anyway..." >&2
    fi

    # Return the operator label via stdout
    echo "$operator_label"
    return 0
}

# Function to download and prepare MLflow operator manifests
# Returns the base config path on stdout, or empty string on failure
download_and_prepare_mlflow_operator_manifests() {
    local mlflow_operator_owner="$1"
    local mlflow_operator_repo="$2"
    local mlflow_operator_branch="$3"

    local mlflow_operator_path="/tmp/mlflow-operator"
    mkdir -p "$mlflow_operator_path"
    echo "Downloading MLflow operator repository from GitHub..." >&2

    # Retry logic: attempt download up to 3 times (1 initial + 2 retries)
    local download_success=false
    local max_attempts=3
    local attempt=1
    local download_url="https://github.com/${mlflow_operator_owner}/${mlflow_operator_repo}/archive/refs/heads/${mlflow_operator_branch}.tar.gz"

    echo "Downloading MLflow operator from: $download_url" >&2

    while [[ $attempt -le $max_attempts && $download_success == false ]]; do
        echo "Download attempt $attempt of $max_attempts..." >&2
        if wget -q "$download_url" -O "$mlflow_operator_path/${mlflow_operator_branch}.tar.gz"; then
            download_success=true
            echo "Download successful on attempt $attempt" >&2
        else
            echo "Download failed on attempt $attempt" >&2
            if [[ $attempt -lt $max_attempts ]]; then
                echo "Retrying in 2 seconds..." >&2
                sleep 2
            fi
        fi
        ((attempt++))
    done

    if [[ $download_success == true ]]; then
        echo "Extracting MLflow operator repository..." >&2
        tar -xzf "$mlflow_operator_path/${mlflow_operator_branch}.tar.gz" -C "$mlflow_operator_path"
        rm -f "$mlflow_operator_path/${mlflow_operator_branch}.tar.gz"

        echo "Preparing MLflow operator manifest source directory..." >&2
        local base_config_path="$mlflow_operator_path/${mlflow_operator_repo}-${mlflow_operator_branch}/config"

        echo "MLflow operator manifest source directory found, processing..." >&2

        # The ODH overlay directory should exist in the downloaded repo. If it doesn't,
        # create a minimal kustomization.yaml pointing at the base so the operator can start.
        local mlflow_operator_odh_overlay_path="$base_config_path/overlays/odh"
        if [[ ! -d "$mlflow_operator_odh_overlay_path" ]]; then
            echo "WARNING: overlays/odh not found in repo — creating minimal fallback kustomization" >&2
            mkdir -p "$mlflow_operator_odh_overlay_path"
            cat > "$mlflow_operator_odh_overlay_path/kustomization.yaml" << 'EOF'
resources:
- ../../../base
EOF
        fi

        # Return the path to the config directory via stdout
        echo "$base_config_path"
        return 0
    else
        echo "Failed to download MLflow operator repository after $max_attempts attempts" >&2
        return 1
    fi
}

# Function to copy MLflow operator manifests to operator pods
copy_mlflow_operator_manifests_to_pods() {
    local base_config_path="$1"
    local operator_label="$2"
    local namespace_name="$3"

    if [[ -d "$base_config_path" ]]; then
        echo "Copying custom MLflow operator manifests to operator pods..."

        # Get all running operator pod names
        local pod_names
        pod_names=$(oc get po -l "$operator_label" -n "$namespace_name" --field-selector=status.phase=Running -o jsonpath="{.items[*].metadata.name}" 2>/dev/null)

        if [[ -n "$pod_names" ]]; then
            # Convert space-separated names to array
            read -ra pod_array <<< "$pod_names"
            local pod_count=${#pod_array[@]}
            echo "Found $pod_count running operator pod(s)"

            local copy_success_count=0
            local copy_failure_count=0

            for pod_name in "${pod_array[@]}"; do
                echo "Copying manifests to pod: $pod_name"
                local full_pod_name="$namespace_name/$pod_name"

                if oc cp "$base_config_path/." "$full_pod_name":"/opt/manifests/mlflow-operator"; then
                    echo "✓ Successfully copied manifests to $pod_name"
                    ((copy_success_count++))
                else
                    echo "✗ Failed to copy manifests to $pod_name"
                    ((copy_failure_count++))
                fi
            done

            echo "Manifest copy summary: $copy_success_count successful, $copy_failure_count failed out of $pod_count pods"

            if [[ $copy_success_count -eq 0 ]]; then
                echo "ERROR: Failed to copy custom manifests to any pods, operator will use default manifests"
                return 1
            elif [[ $copy_failure_count -gt 0 ]]; then
                echo "WARNING: Some pods failed to receive custom manifests, they will use default manifests"
                return 2
            else
                echo "All operator pods successfully received custom manifests"
                return 0
            fi
        else
            echo "Warning: Could not find any running operator pods, skipping manifest copy"
            return 3
        fi
    else
        echo "No custom MLflow operator manifests found at $base_config_path, operator will use default manifests"
        return 4
    fi
}

# Function to wait for MLflow operator controller manager pod to be ready
wait_for_mlflow_operator_controller_manager() {
    local NAMESPACE_NAME="$1"

    echo "Waiting for MLflow operator controller manager pod to be ready..."
    local mlflow_operator_pod_ready=false
    local max_wait_time=300  # 5 minutes
    local wait_interval=10   # Check every 10 seconds
    local elapsed_time=0

    while [[ $elapsed_time -lt $max_wait_time ]]; do
        # Check if the MLflow operator controller manager pod exists and is running
        local mlflow_operator_pod_status=$(oc get pods -n "$NAMESPACE_NAME" --field-selector=status.phase=Running --no-headers 2>/dev/null | grep "mlflow-operator-controller-manager" | wc -l)

        if [[ $mlflow_operator_pod_status -gt 0 ]]; then
            echo "✓ MLflow operator controller manager pod is running"
            mlflow_operator_pod_ready=true
            break
        else
            echo "Waiting for MLflow operator controller manager pod... (${elapsed_time}s/${max_wait_time}s)"
            sleep $wait_interval
            elapsed_time=$((elapsed_time + wait_interval))
        fi
    done

    if [[ $mlflow_operator_pod_ready == false ]]; then
        echo "WARNING: MLflow operator controller manager pod did not become ready within ${max_wait_time} seconds"
        echo "This may indicate an issue with the MLflow operator deployment, but continuing..."
        return 1
    fi

    return 0
}

find_csv_and_update() {
    local MLFLOW_OPERATOR_OWNER="$1"
    local MLFLOW_OPERATOR_REPO="$2"
    local MLFLOW_OPERATOR_BRANCH="$3"

    # Check and update operator images
    echo "Checking for operator namespaces and updating CSV images..."

    # Get list of all namespaces
    NAMESPACE_LIST=$(oc get namespaces -o jsonpath='{.items[*].metadata.name}')

    # Check for redhat-ods-operator namespace
    if echo "$NAMESPACE_LIST" | grep -q "redhat-ods-operator"; then
        echo "Found redhat-ods-operator namespace, updating rhods-operator CSV..."

        # Get CSV matching rhods-operator*
        RHODS_CSV=$(oc get csv -n redhat-ods-operator --no-headers | grep "rhods-operator" | awk '{print $1}' | head -1)

        if [[ -n "$RHODS_CSV" ]]; then
            echo "Found RHODS CSV: $RHODS_CSV"
            patch_csv "$RHODS_CSV" "redhat-ods-operator" "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH"
         else
            echo "No rhods-operator CSV found in redhat-ods-operator namespace"
            exit 1
        fi
    # Check for openshift-operators namespace
    elif echo "$NAMESPACE_LIST" | grep -q "openshift-operators"; then
        echo "Found openshift-operators namespace, updating opendatahub-operator CSV..."

        # Get CSV matching opendatahub-operator*
        ODH_CSV=$(oc get csv -n openshift-operators --no-headers | grep "opendatahub-operator" | awk '{print $1}' | head -1)

        if [[ -n "$ODH_CSV" ]]; then
            echo "Found ODH CSV: $ODH_CSV"
            patch_csv "$ODH_CSV" "openshift-operators" "$MLFLOW_OPERATOR_OWNER" "$MLFLOW_OPERATOR_REPO" "$MLFLOW_OPERATOR_BRANCH"
        else
            echo "No opendatahub-operator CSV found in openshift-operators namespace"
            exit 1
        fi
    else
      echo "No RHOAI or ODH operator found, exiting..."
      exit 1
    fi

    echo "Finished checking and updating operator CSV images"
}
