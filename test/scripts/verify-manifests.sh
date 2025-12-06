#!/bin/bash
# This script verifies that all manifest builds succeed
# Prerequisites:
#   - kustomize must be installed (run 'make kustomize' first)
#   - helm must be installed

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "========================================"
echo "Manifest Build Verification"
echo "========================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if [ ! -f "bin/kustomize" ]; then
    echo -e "${RED}ERROR: kustomize not found at bin/kustomize${NC}"
    echo "Run 'make kustomize' to install it"
    exit 1
fi
echo -e "${GREEN}✅ kustomize found${NC}"

if ! command -v helm &> /dev/null; then
    echo -e "${RED}ERROR: helm not found${NC}"
    echo "Please install helm: https://helm.sh/docs/intro/install/"
    exit 1
fi
echo -e "${GREEN}✅ helm found${NC}"
echo ""

# Track overall status
OVERALL_EXIT_CODE=0

# ==========================================
# Step 1: Verify Helm charts
# ==========================================
echo "Step 1: Verifying Helm charts"
echo "----------------------------------------------"

HELM_EXIT_CODE=0
VALIDATED_CHARTS=()

for chart_dir in charts/*/; do
    if [ -f "${chart_dir}Chart.yaml" ]; then
        chart_name=$(basename "$chart_dir")
        echo "Checking chart: $chart_name"

        # Lint the chart
        echo "  Linting..."
        if helm lint "$chart_dir" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Lint passed${NC}"
        else
            echo -e "  ${RED}✗ Lint failed${NC}"
            helm lint "$chart_dir"
            HELM_EXIT_CODE=1
        fi

        # Template render the chart
        echo "  Rendering template..."
        if helm template test "$chart_dir" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Template renders successfully${NC}"
            VALIDATED_CHARTS+=("$chart_name")
        else
            echo -e "  ${RED}✗ Template failed to render${NC}"
            helm template test "$chart_dir"
            HELM_EXIT_CODE=1
        fi
        echo ""
    fi
done

if [ $HELM_EXIT_CODE -ne 0 ]; then
    echo -e "${RED}ERROR: One or more Helm charts failed validation${NC}"
    OVERALL_EXIT_CODE=1
else
    echo -e "${GREEN}✅ All Helm charts validated successfully${NC}"
fi
echo ""

# ==========================================
# Step 2: Verify config/base builds
# ==========================================
echo "Step 2: Verifying config/base"
echo "----------------------------------------------"

echo "Building config/base..."
if bin/kustomize build config/base > /dev/null 2>&1; then
    echo -e "${GREEN}✓ config/base builds successfully${NC}"
else
    echo -e "${RED}✗ config/base failed to build${NC}"
    bin/kustomize build config/base
    OVERALL_EXIT_CODE=1
fi
echo ""

# ==========================================
# Step 3: Verify kustomize overlays build
# ==========================================
echo "Step 3: Verifying kustomize overlays"
echo "----------------------------------------------"

OVERLAY_EXIT_CODE=0
VALIDATED_OVERLAYS=()

for overlay in config/overlays/*/; do
    overlay_name=$(basename "$overlay")
    echo "Building overlay: $overlay_name"
    if bin/kustomize build "$overlay" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ $overlay_name builds successfully${NC}"
        VALIDATED_OVERLAYS+=("$overlay_name")
    else
        echo -e "${RED}✗ $overlay_name failed to build${NC}"
        bin/kustomize build "$overlay"
        OVERLAY_EXIT_CODE=1
    fi
done

if [ $OVERLAY_EXIT_CODE -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: One or more kustomize overlays failed to build${NC}"
    echo "Please fix the kustomization.yaml files and try again."
    OVERALL_EXIT_CODE=1
else
    echo ""
    echo -e "${GREEN}✅ All kustomize overlays build successfully${NC}"
fi
echo ""

# ==========================================
# Summary
# ==========================================
echo "========================================"
echo "Build Verification Summary"
echo "========================================"
echo ""

if [ ${#VALIDATED_CHARTS[@]} -gt 0 ]; then
    echo -e "${BLUE}Helm Charts:${NC}"
    for chart_dir in charts/*/; do
        if [ -f "${chart_dir}Chart.yaml" ]; then
            chart_name=$(basename "$chart_dir")
            version=$(grep '^version:' "${chart_dir}Chart.yaml" | awk '{print $2}')
            if printf '%s\n' "${VALIDATED_CHARTS[@]}" | grep -qx -- "$chart_name"; then
                echo -e "  ${GREEN}✓${NC} $chart_name (v$version)"
            else
                echo -e "  ${RED}✗${NC} $chart_name (v$version)"
            fi
        fi
    done
    echo ""
fi

if [ ${#VALIDATED_OVERLAYS[@]} -gt 0 ]; then
    echo -e "${BLUE}Kustomize Overlays:${NC}"
    for overlay in config/overlays/*/; do
        overlay_name=$(basename "$overlay")
        if printf '%s\n' "${VALIDATED_OVERLAYS[@]}" | grep -qx -- "$overlay_name"; then
            echo -e "  ${GREEN}✓${NC} $overlay_name"
        else
            echo -e "  ${RED}✗${NC} $overlay_name"
        fi
    done
    echo ""
fi

if [ $OVERALL_EXIT_CODE -eq 0 ]; then
    echo "========================================"
    echo -e "${GREEN}✅ All manifests validated successfully!${NC}"
    echo "========================================"
else
    echo "========================================"
    echo -e "${RED}❌ Some manifests failed validation${NC}"
    echo "========================================"
    exit 1
fi
