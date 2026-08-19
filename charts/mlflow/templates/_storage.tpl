{{/* Workload-specific persistent storage predicates. */}}
{{- define "mlflow.backendUsesStorage" -}}
{{- if or .Values.mlflow.backendStoreUriFrom (hasPrefix "sqlite://" .Values.mlflow.backendStoreUri) (hasPrefix "sqlite+" .Values.mlflow.backendStoreUri) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.registryUsesStorage" -}}
{{- if or .Values.mlflow.registryStoreUriFrom (hasPrefix "sqlite://" (.Values.mlflow.registryStoreUri | default "")) (hasPrefix "sqlite+" (.Values.mlflow.registryStoreUri | default "")) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.readReplicaUsesStorage" -}}
{{- if or .Values.mlflow.readReplicaBackendStoreUriFrom (hasPrefix "sqlite://" (.Values.mlflow.readReplicaBackendStoreUri | default "")) (hasPrefix "sqlite+" (.Values.mlflow.readReplicaBackendStoreUri | default "")) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.trackingMountsStorage" -}}
{{- if and .Values.storage.enabled (not .Values.artifactsServer.enabled) (or (eq (include "mlflow.backendUsesStorage" .) "true") (eq (include "mlflow.registryUsesStorage" .) "true") (eq (include "mlflow.readReplicaUsesStorage" .) "true") (and .Values.mlflow.serveArtifacts (hasPrefix "file://" .Values.mlflow.artifactsDestination))) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.artifactsMountsStorage" -}}
{{- if and .Values.storage.enabled .Values.artifactsServer.enabled (hasPrefix "file://" .Values.artifactsServer.artifactsDestination) -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.garbageCollectionMountsStorage" -}}
{{- if and .Values.storage.enabled (eq (include "mlflow.backendUsesStorage" .) "true") -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "mlflow.traceArchivalMountsStorage" -}}
{{- if and .Values.storage.enabled (or (and .Values.traceArchival.location (hasPrefix "file://" .Values.traceArchival.location)) (eq (include "mlflow.backendUsesStorage" .) "true") (eq (include "mlflow.registryUsesStorage" .) "true")) -}}true{{- else -}}false{{- end -}}
{{- end -}}

