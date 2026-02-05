{{/*
CA Bundle helper template.
Provides shell functions for combining multiple CA bundle files.

Required environment variables:
  CA_BUNDLE_SOURCES - space-separated list of source file paths
  CA_BUNDLE_OUTPUT  - path to the combined output file

Functions provided:
  compute_checksum   - compute SHA256 of all source files
  combine_ca_bundles - concatenate sources into output file
*/}}
{{- define "mlflow.caBundleFunctions" -}}
# Compute checksum of CA bundle source files
compute_checksum() {
  (
    for src in $CA_BUNDLE_SOURCES; do
      cat "$src" 2>/dev/null || true
    done
  ) | sha256sum | cut -d' ' -f1
}

# Combine CA bundle files into a single PEM file
combine_ca_bundles() {
  local output="${CA_BUNDLE_OUTPUT}"
  local temp="${output}.tmp"
  local count=0

  # Initialize temp file
  echo -n "" > "$temp"

  # Concatenate all source bundles
  for src in $CA_BUNDLE_SOURCES; do
    if [ -f "$src" ]; then
      cat "$src" >> "$temp"
      echo "" >> "$temp"
      count=$((count + 1))
    fi
  done

  # Atomically replace the output file
  mv "$temp" "$output"

  echo "Combined $count CA bundle sources into $output"
  echo "Certificate count: $(grep -c 'BEGIN CERTIFICATE' "$output" || echo 0)"
}
{{- end -}}
