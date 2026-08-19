#!/bin/bash
# This script verifies that all manifest builds succeed
# Prerequisites:
#   - kustomize must be installed (run 'make kustomize' first)
#   - helm must be installed

set -euo pipefail

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

        DIRECT_URI_SETS="mlflow.backendStoreUri=sqlite:////mlflow/mlflow.db,mlflow.readReplicaBackendStoreUri=sqlite:////mlflow/mlflow-read.db,mlflow.registryStoreUri=sqlite:////mlflow/mlflow.db"
        SECRETREF_SETS="mlflow.backendStoreUriFrom.secretKeyRef.name=db-creds,mlflow.backendStoreUriFrom.secretKeyRef.key=backend-uri,mlflow.readReplicaBackendStoreUriFrom.secretKeyRef.name=db-creds,mlflow.readReplicaBackendStoreUriFrom.secretKeyRef.key=read-replica-uri,mlflow.registryStoreUriFrom.secretKeyRef.name=db-creds,mlflow.registryStoreUriFrom.secretKeyRef.key=backend-uri"
        chart_failed=0

        # Lint the chart (direct URI path)
        echo "  Linting (direct backend/registry URIs)..."
        if helm lint "$chart_dir" --set "$DIRECT_URI_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Lint passed${NC}"
        else
            echo -e "  ${RED}✗ Lint failed${NC}"
            helm lint "$chart_dir" --set "$DIRECT_URI_SETS" || true
            chart_failed=1
        fi

        # Lint the chart (secretKeyRef path)
        echo "  Linting (backend/registry secret refs)..."
        if helm lint "$chart_dir" --set "$SECRETREF_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Lint passed${NC}"
        else
            echo -e "  ${RED}✗ Lint failed${NC}"
            helm lint "$chart_dir" --set "$SECRETREF_SETS" || true
            chart_failed=1
        fi

        # Template render the chart (direct URI path)
        echo "  Rendering template (direct backend/registry URIs)..."
        if helm template test "$chart_dir" --set "$DIRECT_URI_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Template renders successfully${NC}"
        else
            echo -e "  ${RED}✗ Template failed to render${NC}"
            helm template test "$chart_dir" --set "$DIRECT_URI_SETS" || true
            chart_failed=1
        fi

        # Template render the chart (secretKeyRef path)
        echo "  Rendering template (backend/registry secret refs)..."
        if helm template test "$chart_dir" --set "$SECRETREF_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Template renders successfully${NC}"
        else
            echo -e "  ${RED}✗ Template failed to render${NC}"
            helm template test "$chart_dir" --set "$SECRETREF_SETS" || true
            chart_failed=1
        fi

        echo "  Rejecting malformed read-replica secret ref..."
        if helm template test "$chart_dir" \
            --set "mlflow.backendStoreUri=sqlite:////mlflow/mlflow.db" \
            --set "mlflow.readReplicaBackendStoreUriFrom.secretKeyRef.name=db-creds" > /dev/null 2>&1; then
            echo -e "  ${RED}✗ Malformed read-replica secret ref was accepted${NC}"
            chart_failed=1
        else
            echo -e "  ${GREEN}✓ Malformed read-replica secret ref rejected${NC}"
        fi

        echo "  Rendering dedicated artifacts-only server..."
        ARTIFACT_SERVER_SETS="mlflow.backendStoreUri=postgresql://db/mlflow,mlflow.serveArtifacts=false,artifactsServer.enabled=true,artifactsServer.artifactsDestination=s3://bucket/artifacts,artifactsServer.artifactRoot=https://mlflow.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts"
        if helm template test "$chart_dir" --set "$ARTIFACT_SERVER_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Dedicated artifact server renders successfully${NC}"
        else
            echo -e "  ${RED}✗ Dedicated artifact server failed to render${NC}"
            helm template test "$chart_dir" --set "$ARTIFACT_SERVER_SETS" || true
            chart_failed=1
        fi

        echo "  Rejecting artifact server without an advertised artifact root..."
        if helm template test "$chart_dir" \
            --set "mlflow.backendStoreUri=postgresql://db/mlflow" \
            --set "mlflow.serveArtifacts=false" \
            --set "artifactsServer.enabled=true" > /dev/null 2>&1; then
            echo -e "  ${RED}✗ Artifact server without artifactRoot was accepted${NC}"
            chart_failed=1
        else
            echo -e "  ${GREEN}✓ Missing artifactRoot rejected${NC}"
        fi

        echo "  Rejecting artifact server without an artifact destination..."
        DESTINATION_ERROR=$(helm template test "$chart_dir" \
            --set "mlflow.backendStoreUri=postgresql://db/mlflow" \
            --set "mlflow.serveArtifacts=false" \
            --set "artifactsServer.enabled=true" \
            --set "artifactsServer.artifactRoot=https://mlflow.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts" \
            --set "artifactsServer.artifactsDestination=null" 2>&1) && destination_accepted=1 || destination_accepted=0
        if [ "$destination_accepted" -eq 1 ]; then
            echo -e "  ${RED}✗ Artifact server without artifactsDestination was accepted${NC}"
            chart_failed=1
        elif grep -q "artifactsServer.artifactsDestination must be set" <<< "$DESTINATION_ERROR"; then
            echo -e "  ${GREEN}✓ Missing artifactsDestination rejected${NC}"
        else
            echo -e "  ${RED}✗ Missing artifactsDestination returned an unexpected error${NC}"
            printf '%s\n' "$DESTINATION_ERROR"
            chart_failed=1
        fi

        echo "  Rejecting simultaneous inline and dedicated artifact serving..."
        if helm template test "$chart_dir" \
            --set "mlflow.backendStoreUri=postgresql://db/mlflow" \
            --set "mlflow.serveArtifacts=true" \
            --set "artifactsServer.enabled=true" \
            --set "artifactsServer.artifactRoot=https://mlflow.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts" > /dev/null 2>&1; then
            echo -e "  ${RED}✗ Mutually exclusive artifact serving modes were accepted${NC}"
            chart_failed=1
        else
            echo -e "  ${GREEN}✓ Mutually exclusive artifact serving modes rejected${NC}"
        fi

        FILE_ARTIFACT_SERVER_SETS="mlflow.backendStoreUri=postgresql://db/mlflow,mlflow.serveArtifacts=false,artifactsServer.enabled=true,artifactsServer.artifactsDestination=file:///mlflow/artifacts,artifactsServer.artifactRoot=https://mlflow.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts,storage.enabled=true"
        echo "  Rendering one file-backed artifact server replica with ReadWriteOnce storage..."
        RWO_SETS="$FILE_ARTIFACT_SERVER_SETS,artifactsServer.replicaCount=1,storage.accessMode=ReadWriteOnce"
        if helm template test "$chart_dir" --set "$RWO_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ One file-backed replica with ReadWriteOnce storage renders successfully${NC}"
        else
            echo -e "  ${RED}✗ One file-backed replica with ReadWriteOnce storage failed to render${NC}"
            helm template test "$chart_dir" --set "$RWO_SETS" || true
            chart_failed=1
        fi

        echo "  Rejecting multiple file-backed artifact server replicas with ReadWriteOnce storage..."
        MULTI_RWO_SETS="$FILE_ARTIFACT_SERVER_SETS,artifactsServer.replicaCount=2,storage.accessMode=ReadWriteOnce"
        MULTI_RWO_ERROR=$(helm template test "$chart_dir" --set "$MULTI_RWO_SETS" 2>&1) && multi_rwo_accepted=1 || multi_rwo_accepted=0
        if [ "$multi_rwo_accepted" -eq 1 ]; then
            echo -e "  ${RED}✗ Multiple file-backed replicas with ReadWriteOnce storage were accepted${NC}"
            chart_failed=1
        elif grep -Fq "multiple file-backed artifact server replicas require storage.accessMode=ReadWriteMany" <<< "$MULTI_RWO_ERROR"; then
            echo -e "  ${GREEN}✓ Multiple file-backed replicas with ReadWriteOnce storage rejected${NC}"
        else
            echo -e "  ${RED}✗ Multiple file-backed replicas with ReadWriteOnce storage returned an unexpected error${NC}"
            printf '%s\n' "$MULTI_RWO_ERROR"
            chart_failed=1
        fi

        echo "  Rendering multiple file-backed artifact server replicas with ReadWriteMany storage..."
        RWX_SETS="$FILE_ARTIFACT_SERVER_SETS,artifactsServer.replicaCount=2,storage.accessMode=ReadWriteMany"
        if helm template test "$chart_dir" --set "$RWX_SETS" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✓ Multiple file-backed replicas with ReadWriteMany storage render successfully${NC}"
        else
            echo -e "  ${RED}✗ Multiple file-backed replicas with ReadWriteMany storage failed to render${NC}"
            helm template test "$chart_dir" --set "$RWX_SETS" || true
            chart_failed=1
        fi

        echo "  Rejecting dedicated artifact serving with SQLite metadata..."
        SQLITE_ERROR=$(helm template test "$chart_dir" --set "$FILE_ARTIFACT_SERVER_SETS,mlflow.backendStoreUri=sqlite:////mlflow/mlflow.db" 2>&1) && sqlite_accepted=1 || sqlite_accepted=0
        if [ "$sqlite_accepted" -eq 1 ]; then
            echo -e "  ${RED}✗ Dedicated artifact serving with SQLite was accepted${NC}"
            chart_failed=1
        elif grep -Fq "artifactsServer cannot be enabled with inline SQLite metadata stores" <<< "$SQLITE_ERROR"; then
            echo -e "  ${GREEN}✓ Dedicated artifact serving with SQLite rejected${NC}"
        else
            echo -e "  ${RED}✗ Dedicated artifact serving with SQLite returned an unexpected error${NC}"
            printf '%s\n' "$SQLITE_ERROR"
            chart_failed=1
        fi

        if [ "$chart_failed" -ne 0 ]; then
            HELM_EXIT_CODE=1
        else
            VALIDATED_CHARTS+=("$chart_name")
        fi
        echo ""
    fi
done

if [ "$HELM_EXIT_CODE" -ne 0 ]; then
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
    bin/kustomize build config/base || true
    OVERALL_EXIT_CODE=1
fi
echo ""

# ==========================================
# Step 3: Verify shipped kustomize overlays build
# ==========================================
echo "Step 3: Verifying shipped kustomize overlays"
echo "----------------------------------------------"

OVERLAY_EXIT_CODE=0
VALIDATED_OVERLAYS=()

for overlay in config/overlays/*/; do
    overlay_name=$(basename "$overlay")

    # Skip kind overlay as it requires runtime TLS certificate generation
    if [ "$overlay_name" = "kind" ]; then
        echo "Skipping overlay: $overlay_name (requires runtime certificate generation)"
        continue
    fi

    echo "Building overlay: $overlay_name"
    if bin/kustomize build "$overlay" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ $overlay_name builds successfully${NC}"
        VALIDATED_OVERLAYS+=("$overlay_name")
    else
        echo -e "${RED}✗ $overlay_name failed to build${NC}"
        bin/kustomize build "$overlay" || true
        OVERLAY_EXIT_CODE=1
    fi
done

if [ "$OVERLAY_EXIT_CODE" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: One or more kustomize overlays failed to build${NC}"
    echo "Please fix the kustomization.yaml files and try again."
    OVERALL_EXIT_CODE=1
else
    echo ""
    echo -e "${GREEN}✅ All shipped kustomize overlays build successfully${NC}"
fi
echo ""

# ==========================================
# Step 4: Verify CI/local test manifests build
# ==========================================
echo "Step 4: Verifying CI/local test manifests"
echo "----------------------------------------------"

TEST_INFRA_EXIT_CODE=0
VALIDATED_TEST_INFRA=()

for overlay in .github/test-infra/overlays/*/; do
    overlay_name=$(basename "$overlay")

    # Skip kind overlay as it requires runtime TLS certificate generation
    if [ "$overlay_name" = "kind" ]; then
        echo "Skipping CI overlay: $overlay_name (requires runtime TLS certificate generation)"
        continue
    fi

    echo "Building CI overlay: $overlay_name"
    if bin/kustomize build "$overlay" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ $overlay_name builds successfully${NC}"
        VALIDATED_TEST_INFRA+=("overlay:$overlay_name")
    else
        echo -e "${RED}✗ $overlay_name failed to build${NC}"
        bin/kustomize build "$overlay" || true
        TEST_INFRA_EXIT_CODE=1
    fi
done

for infra_root in .github/test-infra/postgres .github/test-infra/seaweedfs; do
    infra_name=$(basename "$infra_root")
    for variant in "$infra_root"/*/; do
        variant_name=$(basename "$variant")
        if [ ! -f "${variant}kustomization.yaml" ] && [ ! -f "${variant}kustomization.yml" ] && [ ! -f "${variant}Kustomization" ]; then
            continue
        fi
        echo "Building CI manifest set: $infra_name/$variant_name"
        if bin/kustomize build "$variant" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ $infra_name/$variant_name builds successfully${NC}"
            VALIDATED_TEST_INFRA+=("$infra_name:$variant_name")
        else
            echo -e "${RED}✗ $infra_name/$variant_name failed to build${NC}"
            bin/kustomize build "$variant" || true
            TEST_INFRA_EXIT_CODE=1
        fi
    done
done

if [ "$TEST_INFRA_EXIT_CODE" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: One or more CI/local test manifests failed to build${NC}"
    OVERALL_EXIT_CODE=1
else
    echo ""
    echo -e "${GREEN}✅ All CI/local test manifests build successfully${NC}"
fi
echo ""

# ==========================================
# Summary
# ==========================================
echo "========================================"
echo "Build Verification Summary"
echo "========================================"
echo ""

if [ "${#VALIDATED_CHARTS[@]}" -gt 0 ]; then
    echo -e "${BLUE}Helm Charts:${NC}"
    for chart_dir in charts/*/; do
        if [ -f "${chart_dir}Chart.yaml" ]; then
            chart_name=$(basename "$chart_dir")
            version=$(awk '/^version:/ {print $2; exit}' "${chart_dir}Chart.yaml")
            if printf '%s\n' "${VALIDATED_CHARTS[@]}" | grep -qx -- "$chart_name"; then
                echo -e "  ${GREEN}✓${NC} $chart_name (v$version)"
            else
                echo -e "  ${RED}✗${NC} $chart_name (v$version)"
            fi
        fi
    done
    echo ""
fi

if [ "${#VALIDATED_OVERLAYS[@]}" -gt 0 ]; then
    echo -e "${BLUE}Shipped Kustomize Overlays:${NC}"
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

if [ "${#VALIDATED_TEST_INFRA[@]}" -gt 0 ]; then
    echo -e "${BLUE}CI/Local Test Manifests:${NC}"
    for entry in "${VALIDATED_TEST_INFRA[@]}"; do
        echo -e "  ${GREEN}✓${NC} $entry"
    done
    echo ""
fi

if [ "$OVERALL_EXIT_CODE" -eq 0 ]; then
    echo "========================================"
    echo -e "${GREEN}✅ All manifests validated successfully!${NC}"
    echo "========================================"
else
    echo "========================================"
    echo -e "${RED}❌ Some manifests failed validation${NC}"
    echo "========================================"
    exit 1
fi
