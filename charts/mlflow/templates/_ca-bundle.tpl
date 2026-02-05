{{/*
CA Bundle helper template.
Provides shell functions for combining multiple CA bundle files.

Required environment variables:
  CA_BUNDLE_SYSTEM_PATH - path to system CA bundle (included first)
  CA_BUNDLE_MOUNT_PATHS - space-separated list of directories to glob for .crt/.pem files
  CA_BUNDLE_OUTPUT      - path to the combined output file

Functions provided:
  compute_checksum   - compute SHA256 of all source files
  combine_ca_bundles - concatenate sources into output file
*/}}
{{- define "mlflow.caBundleFunctions" -}}
# Compute checksum of CA bundle source files
compute_checksum() {
  (
    # Include system CA
    cat "$CA_BUNDLE_SYSTEM_PATH" 2>/dev/null || true
    # Glob .crt and .pem files from each mount path
    for dir in $CA_BUNDLE_MOUNT_PATHS; do
      for f in "$dir"/*.crt "$dir"/*.pem; do
        [ -f "$f" ] && cat "$f" 2>/dev/null || true
      done
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

  # Include system CA bundle first
  if [ -f "$CA_BUNDLE_SYSTEM_PATH" ]; then
    cat "$CA_BUNDLE_SYSTEM_PATH" >> "$temp"
    echo "" >> "$temp"
    count=$((count + 1))
  fi

  # Glob .crt and .pem files from each mount path
  for dir in $CA_BUNDLE_MOUNT_PATHS; do
    for f in "$dir"/*.crt "$dir"/*.pem; do
      if [ -f "$f" ]; then
        cat "$f" >> "$temp"
        echo "" >> "$temp"
        count=$((count + 1))
      fi
    done
  done

  # Atomically replace the output file
  mv "$temp" "$output"

  echo "Combined $count CA bundle sources into $output"
  echo "Certificate count: $(grep -c 'BEGIN CERTIFICATE' "$output" || echo 0)"
}
{{- end -}}
