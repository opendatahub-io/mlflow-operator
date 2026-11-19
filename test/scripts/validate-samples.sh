#!/bin/bash
# This script validates MLflow sample manifests against the CRD (CR samples)
# and the cluster schema (non-CR samples such as the prepopulation Job).
# Prerequisites:
#   - Kind cluster must be running
#   - kubectl must be configured to connect to the cluster
#   - CRD manifests must be generated (run 'make manifests' first)
#   - python3 with PyYAML (for kind-based sample discovery)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Function to parse CRD configuration string (name|crd_source|crd_resource_name)
# Sets global variables: crd_name, crd_source, crd_resource_name
parse_crd_config() {
    local crd_config=$1
    local old_ifs=$IFS
    IFS='|' read -r crd_name crd_source crd_resource_name <<< "$crd_config"
    IFS=$old_ifs
}

echo "========================================"
echo "MLflow Sample CR Validation"
echo "========================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}ERROR: kubectl not found${NC}"
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}ERROR: Cannot connect to kubernetes cluster${NC}"
    echo "Make sure your kind cluster is running and kubectl is configured"
    exit 1
fi
echo -e "${GREEN}✅ Kubernetes cluster is accessible${NC}"
echo ""

# Discover all sample files by parsing each document's kind. String matching
# like `grep "kind: Job"` would silently skip valid manifests whose formatting
# differs (quotes, indentation, comments), so classification always goes
# through the YAML parser in classify_samples.py.
echo "Discovering sample files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! python3 -c "import yaml" &>/dev/null; then
    echo -e "${RED}ERROR: PyYAML is required for sample discovery (python3 -m pip install pyyaml)${NC}"
    exit 1
fi
SAMPLE_KINDS_FILE="$(mktemp)"
trap 'rm -f "$SAMPLE_KINDS_FILE"' EXIT
if ! python3 "$SCRIPT_DIR/classify_samples.py" config/samples > "$SAMPLE_KINDS_FILE"; then
    echo -e "${RED}ERROR: Failed to classify samples in config/samples/${NC}"
    exit 1
fi

# One entry per file, preserving the kind sets each file contributes. The
# classifier also emits each document's declared namespace (or "-"), so the
# validation loop below can create those namespaces first: server-side dry
# runs still pass through namespace admission, and a namespaced sample such
# as the prepopulation Job would otherwise fail on a fresh Kind cluster that
# never created its namespace.
mapfile -t SAMPLE_FILES < <(cut -f3 "$SAMPLE_KINDS_FILE" | sort -u)
declare -A SAMPLE_FILE_KINDS
while IFS=$'\t' read -r sample_kind sample_namespace sample_file; do
    SAMPLE_FILE_KINDS["$sample_file"]+="$sample_kind "
done < "$SAMPLE_KINDS_FILE"

MLFLOW_SAMPLES=()
MLFLOWCONFIG_SAMPLES=()
OTHER_SAMPLES=()
for sample in "${SAMPLE_FILES[@]}"; do
    sample_kinds="${SAMPLE_FILE_KINDS["$sample"]}"
    case "$sample_kinds" in
        *MLflow\ *) MLFLOW_SAMPLES+=("$sample") ;;
    esac
    case "$sample_kinds" in
        *MLflowConfig\ *) MLFLOWCONFIG_SAMPLES+=("$sample") ;;
    esac
    if [[ "$sample_kinds" != *"MLflow "* && "$sample_kinds" != *"MLflowConfig "* ]]; then
        OTHER_SAMPLES+=("$sample")
    fi
done

if [ "${#SAMPLE_FILES[@]}" -eq 0 ]; then
    echo -e "${RED}ERROR: No samples found in config/samples/${NC}"
    exit 1
fi

if [ "${#MLFLOW_SAMPLES[@]}" -gt 0 ]; then
    echo "Found ${#MLFLOW_SAMPLES[@]} MLflow sample(s)"
fi
if [ "${#MLFLOWCONFIG_SAMPLES[@]}" -gt 0 ]; then
    echo "Found ${#MLFLOWCONFIG_SAMPLES[@]} MLflowConfig sample(s)"
fi
if [ "${#OTHER_SAMPLES[@]}" -gt 0 ]; then
    echo "Found ${#OTHER_SAMPLES[@]} non-CR sample(s): ${OTHER_SAMPLES[*]}"
fi
echo ""

# ==========================================
# Step 1: Install CRDs and validate samples
# ==========================================
echo "Step 1: Installing CRDs and validating samples"
echo "----------------------------------------------"

# CRD configuration: (name|crd_source|crd_resource_name)
CRDS=(
    "MLflow|config/crd/bases/mlflow.opendatahub.io_mlflows.yaml|mlflows.mlflow.opendatahub.io"
    "MLflowConfig|config/crd/mlflow.kubeflow.org_mlflowconfigs.yaml|mlflowconfigs.mlflow.kubeflow.org"
)

# Install CRDs
for crd_config in "${CRDS[@]}"; do
    parse_crd_config "$crd_config"
    
    if [ ! -f "$crd_source" ]; then
        echo -e "${RED}ERROR: CRD file not found at $crd_source${NC}"
        if [ "$crd_name" = "MLflow" ]; then
            echo "Run 'make manifests' to generate CRD files"
        else
            echo "Ensure the vendored CRD is checked in at $crd_source"
        fi
        exit 1
    fi
    
    echo "Installing $crd_name CRD..."
    if kubectl apply -f "$crd_source"; then
        echo -e "${GREEN}✅ $crd_name CRD installed successfully${NC}"
    else
        echo -e "${RED}❌ $crd_name CRD installation failed${NC}"
        exit 1
    fi
done
echo ""

# Wait for CRDs to be established
echo "Waiting for CRDs to be established..."
for crd_config in "${CRDS[@]}"; do
    parse_crd_config "$crd_config"
    
    if ! kubectl wait --for condition=established --timeout=60s "crd/$crd_resource_name" 2>/dev/null; then
        echo -e "${RED}❌ CRD $crd_resource_name failed to become established${NC}"
        exit 1
    fi
done
echo ""

# Namespaced samples dry-run against namespace admission, so create every
# declared namespace (idempotently) before validating. Samples without a
# namespace are applied into the default namespace by the dry run.
mapfile -t SAMPLE_NAMESPACES < <(cut -f2 "$SAMPLE_KINDS_FILE" | grep -v '^-$' | sort -u)
for sample_namespace in "${SAMPLE_NAMESPACES[@]}"; do
    echo "Ensuring namespace ${sample_namespace} exists..."
    if ! kubectl create namespace "$sample_namespace" --dry-run=client -o yaml \
        | kubectl apply -f - > /dev/null; then
        echo -e "${RED}ERROR: Failed to ensure namespace ${sample_namespace}${NC}"
        exit 1
    fi
done
if [ "${#SAMPLE_NAMESPACES[@]}" -gt 0 ]; then
    echo ""
fi

# Validate all samples (CR samples against their installed CRDs, non-CR
# samples such as Jobs against built-in cluster schema)
FAILED=0
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}" "${OTHER_SAMPLES[@]}"; do
    echo "Validating $sample..."
    if kubectl apply --dry-run=server -f "$sample" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ $sample is valid${NC}"
    else
        echo -e "${RED}❌ $sample validation failed:${NC}"
        kubectl apply --dry-run=server -f "$sample" 2>&1
        FAILED=1
    fi
    echo ""
done

if [ $FAILED -eq 1 ]; then
    echo -e "${RED}ERROR: One or more samples failed validation${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All samples validated successfully${NC}"
echo ""

# ==========================================
# Step 2: Verify samples are documented
# ==========================================
echo "Step 2: Verifying samples are documented in AGENTS.md"
echo "------------------------------------------------------"

# Check that AGENTS.md exists
if [ ! -f "AGENTS.md" ]; then
    echo -e "${RED}ERROR: AGENTS.md not found in repository root${NC}"
    exit 1
fi
echo -e "${GREEN}✅ AGENTS.md exists${NC}"
echo ""

# Check that all sample files are mentioned in AGENTS.md
MISSING=0
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}" "${OTHER_SAMPLES[@]}"; do
    sample_name=$(basename "$sample")
    if ! grep -q "$sample_name" AGENTS.md; then
        echo -e "${RED}❌ Sample $sample_name is not documented in AGENTS.md${NC}"
        MISSING=1
    else
        echo -e "${GREEN}✅ $sample_name is documented in AGENTS.md${NC}"
    fi
done

if [ $MISSING -eq 1 ]; then
    echo -e "${RED}ERROR: Some samples are not documented in AGENTS.md${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All samples are documented${NC}"
echo ""

# ==========================================
# Step 3: Verify kustomization.yaml lists all samples
# ==========================================
echo "Step 3: Verifying kustomization.yaml references all samples"
echo "------------------------------------------------------------"

# Check that all samples are either in resources or commented in kustomization.yaml
MISSING=0
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}" "${OTHER_SAMPLES[@]}"; do
    sample_name=$(basename "$sample")
    if ! grep -q "$sample_name" config/samples/kustomization.yaml; then
        echo -e "${RED}❌ Sample $sample_name is not referenced in kustomization.yaml${NC}"
        MISSING=1
    else
        echo -e "${GREEN}✅ $sample_name is referenced in kustomization.yaml${NC}"
    fi
done

if [ $MISSING -eq 1 ]; then
    echo -e "${RED}ERROR: Some samples are not referenced in kustomization.yaml${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All samples are referenced in kustomization.yaml${NC}"
echo ""

# ==========================================
# Summary
# ==========================================
echo "========================================"
echo -e "${GREEN}✅ All validation checks passed!${NC}"
echo "========================================"
