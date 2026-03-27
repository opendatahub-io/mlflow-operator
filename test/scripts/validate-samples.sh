#!/bin/bash
# This script validates MLflow sample CRs against the CRD
# Prerequisites:
#   - Kind cluster must be running
#   - kubectl must be configured to connect to the cluster
#   - CRD manifests must be generated (run 'make manifests' first)

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

# Discover all sample files
echo "Discovering sample files..."
mapfile -t MLFLOW_SAMPLES < <(grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null | grep -v mlflowconfig || true)
mapfile -t MLFLOWCONFIG_SAMPLES < <(grep -l "kind: MLflowConfig" config/samples/*.yaml 2>/dev/null || true)

if [ "${#MLFLOW_SAMPLES[@]}" -eq 0 ] && [ "${#MLFLOWCONFIG_SAMPLES[@]}" -eq 0 ]; then
    echo -e "${RED}ERROR: No samples found in config/samples/${NC}"
    exit 1
fi

if [ "${#MLFLOW_SAMPLES[@]}" -gt 0 ]; then
    echo "Found ${#MLFLOW_SAMPLES[@]} MLflow sample(s)"
fi
if [ "${#MLFLOWCONFIG_SAMPLES[@]}" -gt 0 ]; then
    echo "Found ${#MLFLOWCONFIG_SAMPLES[@]} MLflowConfig sample(s)"
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

# Validate all samples
FAILED=0
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}"; do
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
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}"; do
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
for sample in "${MLFLOW_SAMPLES[@]}" "${MLFLOWCONFIG_SAMPLES[@]}"; do
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
