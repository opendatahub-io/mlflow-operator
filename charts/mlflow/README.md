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

## Dedicated artifact server

Set `artifactsServer.enabled=true` to render a separate MLflow Deployment and Service running
with `--artifacts-only`. Also set `mlflow.serveArtifacts=false`, configure
`artifactsServer.artifactsDestination`, and set `artifactsServer.artifactRoot` to the externally
reachable artifact API URL that the tracking server should advertise. For example:

```yaml
mlflow:
  serveArtifacts: false
artifactsServer:
  enabled: true
  artifactsDestination: s3://mlflow-artifacts
  artifactRoot: https://mlflow.example.com/mlflow-artifacts/api/2.0/mlflow-artifacts/artifacts
```

Dedicated artifact serving requires remote SQL metadata stores; inline SQLite metadata URIs are
rejected. The tracking Deployment does not mount the PVC in this mode. When
`artifactsServer.artifactsDestination` uses `file://`, set `storage.enabled=true`; one artifact
replica may use `ReadWriteOnce`, while multiple replicas require `ReadWriteMany`. Remote artifact
destinations such as S3 do not mount persistent storage.
`temporaryStorage.sizeLimit` configures the writable `/tmp` `emptyDir` in both server pods and
defaults to `1Gi`; increase it for larger or more concurrent proxied artifact transfers.
When garbage collection is enabled, the CronJob resolves `mlflow-artifacts:/` locations through
the internal artifacts-only Service and its static prefix. Without a dedicated server, it uses the
tracking Service and tracking static prefix instead.

The standalone chart creates only the artifacts Deployment and Service. It does not create an
Ingress or `HTTPRoute`; expose the Service at the configured `artifactRoot` yourself. Both servers
use the same image, Kubernetes workspace provider, artifact credentials, scheduling settings, and
security contexts. The artifacts-only container explicitly clears backend, registry, and
read-replica store URI variables, including values imported by shared `envFrom` sources. Provide
the TLS Secret configured by `artifactsServer.tls.secretName` when
it is not provisioned by an OpenShift service-ca annotation.

See `values.yaml` for the full list of configurable settings.
