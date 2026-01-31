{{/*
CA Bundle combine script.
This script combines system, platform, and custom CA bundles into a single PEM file.
Used by both the init container (initial creation) and sidecar (live updates).
*/}}
{{- define "mlflow.caBundleScript" -}}
set -e

COMBINED_BUNDLE="{{ .Values.caBundle.filePath }}"
SYSTEM_BUNDLE="{{ .Values.caBundle.systemBundlePath }}"
TEMP_BUNDLE="${COMBINED_BUNDLE}.tmp"

# Write to temp file first for atomic update
# Start with system CA bundle if it exists
if [ -f "$SYSTEM_BUNDLE" ]; then
  echo "# System CA bundle" > "$TEMP_BUNDLE"
  cat "$SYSTEM_BUNDLE" >> "$TEMP_BUNDLE"
  echo "" >> "$TEMP_BUNDLE"
else
  echo "# Combined CA bundle" > "$TEMP_BUNDLE"
fi

{{- if .Values.platformCABundle.enabled }}
# Append platform CA bundle if it exists
PLATFORM_BUNDLE="{{ .Values.platformCABundle.filePath }}"
if [ -f "$PLATFORM_BUNDLE" ]; then
  echo "# Platform CA bundle" >> "$TEMP_BUNDLE"
  cat "$PLATFORM_BUNDLE" >> "$TEMP_BUNDLE"
  echo "" >> "$TEMP_BUNDLE"
fi
# Also check for additional platform-specific certs
PLATFORM_EXTRA="{{ .Values.platformCABundle.extraFilePath }}"
if [ -f "$PLATFORM_EXTRA" ]; then
  echo "# Platform additional CA bundle" >> "$TEMP_BUNDLE"
  cat "$PLATFORM_EXTRA" >> "$TEMP_BUNDLE"
  echo "" >> "$TEMP_BUNDLE"
fi
{{- end }}

{{- if .Values.caBundleConfigMap.enabled }}
# Append user-provided custom CA bundle if it exists
CUSTOM_BUNDLE="/custom-certs/custom-ca-bundle.crt"
if [ -f "$CUSTOM_BUNDLE" ]; then
  echo "# Custom CA bundle" >> "$TEMP_BUNDLE"
  cat "$CUSTOM_BUNDLE" >> "$TEMP_BUNDLE"
  echo "" >> "$TEMP_BUNDLE"
fi
{{- end }}

# Atomically replace the combined bundle
mv "$TEMP_BUNDLE" "$COMBINED_BUNDLE"

echo "Combined CA bundle created at $COMBINED_BUNDLE"
echo "Certificate count: $(grep -c 'BEGIN CERTIFICATE' "$COMBINED_BUNDLE" || echo 0)"
{{- end }}
