#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <summary-file>" >&2
  exit 1
fi

SUMMARY_FILE=$1
MARKER='<!-- operator-chaos-summary -->'
MAX_COMMENT_CHARS=60000

if [[ ! -f "$SUMMARY_FILE" ]]; then
  echo "summary file not found: $SUMMARY_FILE" >&2
  exit 1
fi

if [[ -z "${PR_NUMBER:-}" ]]; then
  echo "PR_NUMBER is required" >&2
  exit 1
fi

SUMMARY_BODY=$(<"$SUMMARY_FILE")
COMMENT_BODY=$(printf '%s\n%s\n' "$MARKER" "$SUMMARY_BODY")

if (( ${#COMMENT_BODY} > MAX_COMMENT_CHARS )); then
  truncation_notice=$'\n\n_Comment truncated to stay within the GitHub comment size limit. See the workflow summary and logs for the full operator-chaos output._\n'
  max_body_chars=$(( MAX_COMMENT_CHARS - ${#truncation_notice} ))
  COMMENT_BODY="${COMMENT_BODY:0:max_body_chars}${truncation_notice}"
fi

existing_comment_id=$(
  gh api "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" --paginate \
    --jq "[.[] | select(.user.type == \"Bot\" and (.body | contains(\"$MARKER\")))] | last | .id // empty"
)

if [[ -n "$existing_comment_id" ]]; then
  gh api \
    --method PATCH \
    -H "Accept: application/vnd.github+json" \
    "repos/${GITHUB_REPOSITORY}/issues/comments/${existing_comment_id}" \
    -f body="$COMMENT_BODY" >/dev/null
else
  gh api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
    -f body="$COMMENT_BODY" >/dev/null
fi
