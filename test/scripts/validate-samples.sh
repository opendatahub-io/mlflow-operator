#!/bin/bash
# This script validates MLflow sample CRs against the CRD
# Prerequisites:
#   - Kind cluster must be running
#   - kubectl must be configured to connect to the cluster
#   - CRD manifests must be generated (run 'make manifests' first)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to parse CRD configuration string (name:crd_file:crd_resource_name)
# Sets global variables: crd_name, crd_file, crd_resource_name
parse_crd_config() {
    local crd_config=$1
    local old_ifs=$IFS
    IFS=':' read -r crd_name crd_file crd_resource_name <<< "$crd_config"
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
MLFLOW_SAMPLES=$(grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null | grep -v mlflowconfig || true)
MLFLOWCONFIG_SAMPLES=$(grep -l "kind: MLflowConfig" config/samples/*.yaml 2>/dev/null || true)

if [ -z "$MLFLOW_SAMPLES" ] && [ -z "$MLFLOWCONFIG_SAMPLES" ]; then
    echo -e "${RED}ERROR: No samples found in config/samples/${NC}"
    exit 1
fi

if [ -n "$MLFLOW_SAMPLES" ]; then
    count=$(echo "$MLFLOW_SAMPLES" | grep -c . || echo "0")
    echo "Found $count MLflow sample(s)"
fi
if [ -n "$MLFLOWCONFIG_SAMPLES" ]; then
    count=$(echo "$MLFLOWCONFIG_SAMPLES" | grep -c . || echo "0")
    echo "Found $count MLflowConfig sample(s)"
fi
echo ""

# ==========================================
# Step 1: Install CRDs and validate samples
# ==========================================
echo "Step 1: Installing CRDs and validating samples"
echo "----------------------------------------------"

# CRD configuration: (name:crd_file:crd_resource_name)
CRDS=(
    "MLflow:config/crd/bases/mlflow.opendatahub.io_mlflows.yaml:mlflows.mlflow.opendatahub.io"
    "MLflowConfig:config/crd/bases/mlflow.opendatahub.io_mlflowconfigs.yaml:mlflowconfigs.mlflow.opendatahub.io"
)

# Install CRDs
for crd_config in "${CRDS[@]}"; do
    parse_crd_config "$crd_config"
    
    if [ ! -f "$crd_file" ]; then
        echo -e "${RED}ERROR: CRD file not found at $crd_file${NC}"
        echo "Run 'make manifests' to generate CRD files"
        exit 1
    fi
    
    echo "Installing $crd_name CRD..."
    if kubectl apply -f "$crd_file"; then
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
for sample in $MLFLOW_SAMPLES $MLFLOWCONFIG_SAMPLES; do
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
for sample in $MLFLOW_SAMPLES $MLFLOWCONFIG_SAMPLES; do
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
for sample in $MLFLOW_SAMPLES $MLFLOWCONFIG_SAMPLES; do
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
