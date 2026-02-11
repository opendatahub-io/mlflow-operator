{{/*
CA Bundle helper templates.
Provides shell functions for combining multiple CA bundle files and helper templates.

Required environment variables:
  CA_BUNDLE_FILE_PATHS  - space-separated list of file paths to include directly
  CA_BUNDLE_MOUNT_PATHS - space-separated list of directories to glob for .crt/.pem files
  CA_BUNDLE_OUTPUT      - path to the combined output file

Functions provided:
  compute_checksum   - compute SHA256 of all source files
  combine_ca_bundles - concatenate sources into output file

Templates provided:
  mlflow.caBundleFilePaths  - extracts filePaths as space-separated string
  mlflow.caBundleMountPaths - extracts mount paths from configMaps as space-separated string
*/}}

{{/*
Extract file paths from caBundle.filePaths as a space-separated string.
Usage: {{ include "mlflow.caBundleFilePaths" . }}
*/}}
{{- define "mlflow.caBundleFilePaths" -}}
{{- .Values.caBundle.filePaths | join " " -}}
{{- end -}}

{{/*
Extract mount paths from caBundle.configMaps as a space-separated string.
Usage: {{ include "mlflow.caBundleMountPaths" . }}
*/}}
{{- define "mlflow.caBundleMountPaths" -}}
{{- $paths := list -}}
{{- range .Values.caBundle.configMaps -}}
{{- $paths = append $paths .mountPath -}}
{{- end -}}
{{- $paths | join " " -}}
{{- end -}}

{{- define "mlflow.caBundleFunctions" -}}
# Compute checksum of CA bundle source files
compute_checksum() {
  (
    # Include file paths (e.g., system CA bundle)
    for f in $CA_BUNDLE_FILE_PATHS; do
      [ -f "$f" ] && cat "$f" 2>/dev/null || true
    done
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

  # Include file paths first (e.g., system CA bundle)
  for f in $CA_BUNDLE_FILE_PATHS; do
    if [ -f "$f" ]; then
      cat "$f" >> "$temp"
      echo "" >> "$temp"
      count=$((count + 1))
    fi
  done

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
