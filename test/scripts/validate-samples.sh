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

# ==========================================
# Step 1: Install CRD and validate samples
# ==========================================
echo "Step 1: Installing CRD and validating samples"
echo "----------------------------------------------"

# CRD file location
CRD_FILE="config/crd/bases/mlflow.opendatahub.io_mlflows.yaml"

if [ ! -f "$CRD_FILE" ]; then
    echo -e "${RED}ERROR: CRD file not found at $CRD_FILE${NC}"
    echo "Run 'make manifests' to generate CRD files"
    exit 1
fi

# Install the CRD
echo "Installing CRD..."
if kubectl apply -f "$CRD_FILE"; then
    echo -e "${GREEN}✅ CRD installed successfully${NC}"
else
    echo -e "${RED}❌ CRD installation failed${NC}"
    exit 1
fi
echo ""

# Wait for CRD to be established
echo "Waiting for CRD to be established..."
kubectl wait --for condition=established --timeout=60s crd/mlflows.mlflow.opendatahub.io
echo ""

# Find all sample files containing "kind: MLflow"
echo "Finding MLflow sample files..."
SAMPLES=$(grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null || true)

if [ -z "$SAMPLES" ]; then
    echo -e "${RED}ERROR: No MLflow samples found in config/samples/${NC}"
    exit 1
fi

echo "Found $(echo "$SAMPLES" | wc -l) MLflow sample(s)"
echo ""

# Validate each sample against the CRD
FAILED=0
for sample in $SAMPLES; do
    echo "Validating $sample..."

    # Use server-side dry-run with installed CRD for full schema validation
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

# Find all sample files containing "kind: MLflow"
SAMPLES=$(grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null || true)

if [ -z "$SAMPLES" ]; then
    echo -e "${RED}ERROR: No MLflow samples found in config/samples/${NC}"
    exit 1
fi

# Check that all sample files are mentioned in AGENTS.md
MISSING=0
for sample in $SAMPLES; do
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

# Find all sample files containing "kind: MLflow"
SAMPLES=$(grep -l "kind: MLflow" config/samples/*.yaml 2>/dev/null || true)

if [ -z "$SAMPLES" ]; then
    echo -e "${RED}ERROR: No MLflow samples found in config/samples/${NC}"
    exit 1
fi

# Check that all samples are either in resources or commented in kustomization.yaml
MISSING=0
for sample in $SAMPLES; do
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
