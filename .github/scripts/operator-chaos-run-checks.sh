#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 <output-dir> <source-knowledge-dir> <target-knowledge-dir> <source-crd-path> <target-crd-path>" >&2
  exit 1
fi

OUTPUT_DIR=$1
SOURCE_KNOWLEDGE_DIR=$2
TARGET_KNOWLEDGE_DIR=$3
SOURCE_CRD_PATH=$4
TARGET_CRD_PATH=$5
OUTPUT_MODE=${OPERATOR_CHAOS_OUTPUT_MODE:-verbose}

mkdir -p "${OUTPUT_DIR}"
cat > "${OUTPUT_DIR}/outcomes.env" <<'EOF'
VALIDATE_OUTCOME=failure
PREFLIGHT_OUTCOME=failure
KNOWLEDGE_DIFF_OUTCOME=failure
CRD_DIFF_OUTCOME=failure
SIMULATE_OUTCOME=failure
EOF

set_outcome() {
  local name=$1
  local value=$2
  printf '%s=%s\n' "${name}" "${value}" >> "${OUTPUT_DIR}/outcomes.env"
}

run_capture() {
  local outcome_name=$1
  local output_file=$2
  shift 2

  local status="success"
  if [[ "${OUTPUT_MODE}" == "verbose" ]]; then
    if ! "$@" 2>&1 | tee "${output_file}"; then
      status="failure"
    fi
  else
    if ! "$@" > "${output_file}" 2>&1; then
      status="failure"
    fi
  fi

  set_outcome "${outcome_name}" "${status}"
}

run_simulation_capture() {
  local output_file=$1
  local status="success"

  if [[ "${OUTPUT_MODE}" == "verbose" ]]; then
    if ! operator-chaos simulate-upgrade \
      --source "${SOURCE_KNOWLEDGE_DIR}" \
      --target "${TARGET_KNOWLEDGE_DIR}" \
      --dry-run 2>&1 | tee "${output_file}"; then
      status="failure"
    fi
  else
    if ! operator-chaos simulate-upgrade \
      --source "${SOURCE_KNOWLEDGE_DIR}" \
      --target "${TARGET_KNOWLEDGE_DIR}" \
      --dry-run 2>&1 | awk '
        /^Source:/ ||
        /^Target:/ ||
        /^Component diffs:/ ||
        /^Generated experiments:/ ||
        /^No differences found\./ ||
        /^No experiments generated/ ||
        /^Error:/ ||
        /skipping simulate-upgrade/
      ' > "${output_file}"; then
      status="failure"
    fi
  fi

  set_outcome "SIMULATE_OUTCOME" "${status}"
}

run_capture "VALIDATE_OUTCOME" "${OUTPUT_DIR}/validate-knowledge.txt" \
  operator-chaos validate --knowledge "${TARGET_KNOWLEDGE_DIR}/mlflow.yaml"

run_capture "PREFLIGHT_OUTCOME" "${OUTPUT_DIR}/preflight.txt" \
  operator-chaos preflight --knowledge "${TARGET_KNOWLEDGE_DIR}/mlflow.yaml" --local

BASE_KNOWLEDGE_FILE="${SOURCE_KNOWLEDGE_DIR}/mlflow.yaml"
if [[ -f "${BASE_KNOWLEDGE_FILE}" ]]; then
  operator-chaos diff \
    --source "${SOURCE_KNOWLEDGE_DIR}" \
    --target "${TARGET_KNOWLEDGE_DIR}" \
    --breaking \
    --format json > "${OUTPUT_DIR}/knowledge-diff.json"
  run_capture "KNOWLEDGE_DIFF_OUTCOME" "${OUTPUT_DIR}/knowledge-diff.txt" \
    operator-chaos diff \
      --source "${SOURCE_KNOWLEDGE_DIR}" \
      --target "${TARGET_KNOWLEDGE_DIR}" \
      --breaking
else
  echo '{"skipped": true}' > "${OUTPUT_DIR}/knowledge-diff.json"
  printf '%s\n' "No base knowledge model found; skipping knowledge diff for bootstrap PR." > "${OUTPUT_DIR}/knowledge-diff.txt"
  if [[ "${OUTPUT_MODE}" == "verbose" ]]; then
    cat "${OUTPUT_DIR}/knowledge-diff.txt"
  fi
  set_outcome "KNOWLEDGE_DIFF_OUTCOME" "success"
fi

if [[ -f "${SOURCE_CRD_PATH}" && -f "${TARGET_CRD_PATH}" ]]; then
  SOURCE_DIR="${OUTPUT_DIR}/source-crds"
  TARGET_DIR="${OUTPUT_DIR}/target-crds"
  rm -rf "${SOURCE_DIR}" "${TARGET_DIR}"
  mkdir -p "${SOURCE_DIR}" "${TARGET_DIR}"
  cp "${SOURCE_CRD_PATH}" "${SOURCE_DIR}/"
  cp "${TARGET_CRD_PATH}" "${TARGET_DIR}/"
  operator-chaos diff-crds \
    --source-crds "${SOURCE_DIR}" \
    --target-crds "${TARGET_DIR}" \
    --format json > "${OUTPUT_DIR}/crd-diff.json"
  run_capture "CRD_DIFF_OUTCOME" "${OUTPUT_DIR}/crd-diff.txt" \
    operator-chaos diff-crds \
      --source-crds "${SOURCE_DIR}" \
      --target-crds "${TARGET_DIR}"
else
  echo '{"skipped": true}' > "${OUTPUT_DIR}/crd-diff.json"
  printf '%s\n' "Base or current MLflow CRD not found; skipping CRD diff for bootstrap PR." > "${OUTPUT_DIR}/crd-diff.txt"
  if [[ "${OUTPUT_MODE}" == "verbose" ]]; then
    cat "${OUTPUT_DIR}/crd-diff.txt"
  fi
  set_outcome "CRD_DIFF_OUTCOME" "success"
fi

if [[ -f "${BASE_KNOWLEDGE_FILE}" ]]; then
  run_simulation_capture "${OUTPUT_DIR}/simulate-upgrade.txt"
else
  printf '%s\n' "No base knowledge model found; skipping simulate-upgrade for bootstrap PR." > "${OUTPUT_DIR}/simulate-upgrade.txt"
  if [[ "${OUTPUT_MODE}" == "verbose" ]]; then
    cat "${OUTPUT_DIR}/simulate-upgrade.txt"
  fi
  set_outcome "SIMULATE_OUTCOME" "success"
fi
