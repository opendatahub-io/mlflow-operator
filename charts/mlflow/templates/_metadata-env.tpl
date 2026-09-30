{{- define "mlflow.metadataStoreEnv" -}}
{{- if or .Values.mlflow.readReplicaBackendStoreUri .Values.mlflow.readReplicaBackendStoreUriFrom }}
- name: MLFLOW_READ_REPLICA_BACKEND_STORE_URI
  {{- if .Values.mlflow.readReplicaBackendStoreUriFrom }}
  valueFrom:
    {{- toYaml .Values.mlflow.readReplicaBackendStoreUriFrom | nindent 4 }}
  {{- else }}
  value: {{ .Values.mlflow.readReplicaBackendStoreUri | quote }}
  {{- end }}
{{- end }}
{{- if or .Values.mlflow.registryStoreUri .Values.mlflow.registryStoreUriFrom }}
- name: MLFLOW_REGISTRY_STORE_URI
  {{- if .Values.mlflow.registryStoreUriFrom }}
  valueFrom:
    {{- toYaml .Values.mlflow.registryStoreUriFrom | nindent 4 }}
  {{- else }}
  value: {{ .Values.mlflow.registryStoreUri | quote }}
  {{- end }}
{{- end }}
{{- end -}}
