#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <runner-temp-dir>" >&2
  exit 1
fi

RUNNER_TEMP_DIR=$1
INCLUDE_DETAILS=${OPERATOR_CHAOS_SUMMARY_INCLUDE_DETAILS:-true}

read_text() {
  local file="$RUNNER_TEMP_DIR/$1"
  if [[ -f "$file" ]]; then
    <"$file" sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
  fi
}

code_block() {
  local text=${1-}
  if [[ -z "$text" ]]; then
    printf '%s\n' "_No output captured._"
  else
    printf '```text\n%s\n```\n' "$text"
  fi
}

validate_text=$(read_text "validate-knowledge.txt")
preflight_text=$(read_text "preflight.txt")
knowledge_diff_text=$(read_text "knowledge-diff.txt")
crd_diff_text=$(read_text "crd-diff.txt")
simulate_text=$(read_text "simulate-upgrade.txt")

knowledge_diff_json="$RUNNER_TEMP_DIR/knowledge-diff.json"
crd_diff_json="$RUNNER_TEMP_DIR/crd-diff.json"

knowledge_skipped=false
knowledge_breaking=""
if [[ -f "$knowledge_diff_json" ]]; then
  knowledge_skipped=$(jq -r '.skipped // false' "$knowledge_diff_json")
  if [[ "$knowledge_skipped" != "true" ]]; then
    knowledge_breaking=$(jq -r '.summary.breakingChanges // ""' "$knowledge_diff_json")
  fi
fi

crd_skipped=false
crd_breaking=""
crd_warning=""
crd_info=""
if [[ -f "$crd_diff_json" ]]; then
  crd_skipped=$(jq -r '.skipped // false' "$crd_diff_json")
  if [[ "$crd_skipped" != "true" ]]; then
    read -r crd_breaking crd_warning crd_info < <(
      jq -r '
        reduce ((.crds // [])[]?.apiVersions[]?.schemaChanges[]?) as $change
          ([0,0,0];
            if $change.severity == "breaking" then .[0] += 1
            elif $change.severity == "warning" then .[1] += 1
            elif $change.severity == "info" then .[2] += 1
            else .
            end
          )
        | @tsv
      ' "$crd_diff_json"
    )
  fi
fi

simulate_skipped=false
generated_experiments=""
if [[ "$simulate_text" == *"skipping simulate-upgrade"* ]]; then
  simulate_skipped=true
else
  generated_experiments=$(sed -n 's/^Generated experiments: \([0-9][0-9]*\)$/\1/p' <<<"$simulate_text" | tail -n 1)
fi

cat <<EOF
## Operator-chaos Summary

- Knowledge validation: \`${VALIDATE_OUTCOME:-unknown}\`
- Local preflight: \`${PREFLIGHT_OUTCOME:-unknown}\`
EOF

if [[ "$knowledge_skipped" == "true" ]]; then
  printf '%s\n' "- Knowledge diff: \`skipped\`"
else
  printf '%s\n' "- Knowledge diff: \`${KNOWLEDGE_DIFF_OUTCOME:-unknown}\`"
  if [[ -n "$knowledge_breaking" ]]; then
    printf '%s\n' "  - Breaking changes reported: \`$knowledge_breaking\`"
  fi
fi

if [[ "$crd_skipped" == "true" ]]; then
  printf '%s\n' "- CRD diff: \`skipped\`"
else
  printf '%s\n' "- CRD diff: \`${CRD_DIFF_OUTCOME:-unknown}\`"
  if [[ -n "$crd_breaking" ]]; then
    printf '%s\n' "  - Schema changes reported: \`$crd_breaking\` breaking, \`$crd_warning\` warning, \`$crd_info\` info"
  fi
fi

if [[ "$simulate_skipped" == "true" ]]; then
  printf '%s\n' "- Upgrade simulation dry-run: \`skipped\`"
else
  printf '%s\n' "- Upgrade simulation dry-run: \`${SIMULATE_OUTCOME:-unknown}\`"
  if [[ -n "$generated_experiments" ]]; then
    printf '%s\n' "  - Generated experiments: \`$generated_experiments\`"
  fi
fi

if [[ "${INCLUDE_DETAILS}" != "false" ]]; then
cat <<EOF

<details><summary>Knowledge validation</summary>

$(code_block "$validate_text")

</details>

<details><summary>Local preflight</summary>

$(code_block "$preflight_text")

</details>

<details><summary>Knowledge diff</summary>

$(code_block "$knowledge_diff_text")

</details>

<details><summary>CRD diff</summary>

$(code_block "$crd_diff_text")

</details>

<details><summary>Upgrade simulation dry-run</summary>

$(code_block "$simulate_text")

</details>
EOF
else
  if [[ "${VALIDATE_OUTCOME:-unknown}" == "failure" || \
        "${PREFLIGHT_OUTCOME:-unknown}" == "failure" || \
        "${KNOWLEDGE_DIFF_OUTCOME:-unknown}" == "failure" || \
        "${CRD_DIFF_OUTCOME:-unknown}" == "failure" || \
        "${SIMULATE_OUTCOME:-unknown}" == "failure" ]]; then
    printf '\n%s\n' "_One or more operator-chaos checks failed. See the workflow logs for details._"
  fi
fi
