# MLflow Helm Chart

This chart deploys MLflow with Kubernetes authentication enabled. TLS is terminated directly in the MLflow pod using uvicorn options; certificates are loaded from `tls.secretName` (on OpenShift this is provided automatically by the service-ca operator).

- Authorization mode defaults to `self_subject_access_review` handled directly by MLflow.
- MLflow listens on port 8443 with TLS.
- Health probes and traffic use HTTPS end-to-end.
- This standalone chart does not orchestrate MLflow database migrations.

Set `mlflow.backendStoreUri` (or `mlflow.backendStoreUriFrom`) explicitly; it is required and should not rely on implicit defaults.

## Read-replica backend routing

With MLflow 3.14 or later, set one optional read-replica URI to route supported tracking and model-registry reads away from the primary database:

```yaml
mlflow:
  backendStoreUriFrom:
    secretKeyRef:
      name: mlflow-db-credentials
      key: backend-store-uri
  readReplicaBackendStoreUriFrom:
    secretKeyRef:
      name: mlflow-db-credentials
      key: read-replica-backend-store-uri
```

For a URI without credentials, `mlflow.readReplicaBackendStoreUri` can be used directly. When neither replica value is set, all operations continue to use the primary backend.

MLflow uses one replica URI for both tracking and model-registry reads. The replica must have a compatible schema, and its availability and data freshness depend on the database topology. This standalone chart does not migrate either database or provide application-level failover to the primary.

See `values.yaml` for the full list of configurable settings.
