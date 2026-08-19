{{/* Internal URI used by mlflow gc to resolve proxied artifact locations. */}}
{{- define "mlflow.garbageCollectionTrackingURI" -}}
{{- if .Values.artifactsServer.enabled -}}
https://mlflow-artifacts{{ .Values.resourceSuffix }}.{{ .Values.namespace }}.svc:{{ .Values.artifactsServer.service.port }}{{ .Values.artifactsServer.staticPrefix | trimSuffix "/" }}
{{- else -}}
https://mlflow{{ .Values.resourceSuffix }}.{{ .Values.namespace }}.svc:{{ .Values.service.port }}{{ .Values.mlflow.staticPrefix | trimSuffix "/" }}
{{- end -}}
{{- end -}}

