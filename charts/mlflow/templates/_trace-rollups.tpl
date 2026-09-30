{{- define "mlflow.traceRollupsSQLBackend" -}}
{{- if and .Values.traceRollups.sqlBackend (or .Values.mlflow.backendStoreUriFrom (hasPrefix "postgresql:" .Values.mlflow.backendStoreUri) (hasPrefix "postgresql+" .Values.mlflow.backendStoreUri) (hasPrefix "mysql:" .Values.mlflow.backendStoreUri) (hasPrefix "mysql+" .Values.mlflow.backendStoreUri)) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "mlflow.hasRollupsEnvOverride" -}}
{{- $found := false -}}
{{- range .Values.env -}}
{{- if eq .name "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED" -}}{{- $found = true -}}{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}
{{- define "mlflow.traceRollupsEnabled" -}}
{{- if and .Values.traceRollups.enabled (eq (include "mlflow.traceRollupsSQLBackend" .) "true") -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}
