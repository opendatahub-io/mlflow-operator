{{- define "mlflow.hasRollupsEnvOverride" -}}
{{- $found := false -}}
{{- range .Values.env -}}
{{- if eq .name "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" -}}{{- $found = true -}}{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}

{{- define "mlflow.hasBackendEnvOverride" -}}
{{- $found := false -}}
{{- range .Values.env -}}
{{- if eq .name "MLFLOW_BACKEND_STORE_URI" -}}{{- $found = true -}}{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}

{{- define "mlflow.referenceValue" -}}
{{- $value := "__UNRESOLVED__" -}}
{{- if .source.secretKeyRef -}}
  {{- $ref := .source.secretKeyRef -}}
  {{- $secret := lookup "v1" "Secret" .namespace $ref.name -}}
  {{- if and $secret (hasKey $secret.data $ref.key) -}}
    {{- $value = (index $secret.data $ref.key | b64dec) -}}
  {{- end -}}
{{- else if .source.configMapKeyRef -}}
  {{- $ref := .source.configMapKeyRef -}}
  {{- $configMap := lookup "v1" "ConfigMap" .namespace $ref.name -}}
  {{- if and $configMap (hasKey $configMap.data $ref.key) -}}
    {{- $value = index $configMap.data $ref.key -}}
  {{- end -}}
{{- end -}}
{{- $value -}}
{{- end -}}

{{- define "mlflow.backendClass" -}}
{{- if .Values.mlflow.backendResolved -}}
  {{- if .Values.mlflow.backendUsesStorage -}}sqlite{{- else -}}remote{{- end -}}
{{- else -}}
  {{- $uri := .Values.mlflow.backendStoreUri -}}
  {{- if .Values.mlflow.backendStoreUriFrom -}}
    {{- $uri = include "mlflow.referenceValue" (dict "source" .Values.mlflow.backendStoreUriFrom "namespace" .Values.namespace) -}}
  {{- end -}}
  {{- range .Values.env -}}
    {{- if eq .name "MLFLOW_BACKEND_STORE_URI" -}}
      {{- if .valueFrom -}}
        {{- $uri = include "mlflow.referenceValue" (dict "source" .valueFrom "namespace" $.Values.namespace) -}}
      {{- else -}}
        {{- $uri = .value | default "" -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
  {{- if or (hasPrefix "postgresql:" $uri) (hasPrefix "postgresql+" $uri) (hasPrefix "mysql:" $uri) (hasPrefix "mysql+" $uri) -}}remote
  {{- else if or (hasPrefix "sqlite://" $uri) (hasPrefix "sqlite+" $uri) -}}sqlite
  {{- else -}}unknown{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mlflow.traceRollupsSQLBackend" -}}
{{- $class := include "mlflow.backendClass" . -}}
{{- if and .Values.traceRollups.schedulingEnabled (eq $class "unknown") -}}
  {{- fail "trace rollup backend cannot be resolved for scheduling; use a resolvable reference or disable scheduling" -}}
{{- end -}}
{{- if and .Values.traceRollups.sqlBackend (eq $class "remote") -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.traceRollupsRuntime" -}}
{{- if .Values.traceRollups.runtimeResolved -}}
  {{- .Values.traceRollups.enabled -}}
{{- else -}}
  {{- $value := printf "%t" .Values.traceRollups.enabled -}}
  {{- range .Values.env -}}
    {{- if eq .name "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" -}}
      {{- if .valueFrom -}}
        {{- $value = include "mlflow.referenceValue" (dict "source" .valueFrom "namespace" $.Values.namespace) -}}
      {{- else -}}
        {{- $value = .value | default "" -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
  {{- $lower := lower $value -}}
  {{- if or (eq $lower "true") (eq $value "1") -}}true
  {{- else if or (eq $lower "false") (eq $value "0") -}}false
  {{- else -}}{{- fail "effective MLFLOW_SQL_TRACE_ROLLUPS_ENABLED must be true, false, 1, or 0 for scheduling" -}}{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mlflow.traceRollupsEnabled" -}}
{{- if and .Values.traceRollups.schedulingEnabled .Values.traceRollups.sqlBackend (ne (include "mlflow.backendClass" .) "sqlite") (eq (include "mlflow.traceRollupsRuntime" .) "true") (eq (include "mlflow.traceRollupsSQLBackend" .) "true") -}}true{{- else -}}false{{- end -}}
{{- end -}}
