# MLflow Helm Chart

This chart deploys MLflow with Kubernetes authentication enabled. TLS is terminated directly in the MLflow pod using uvicorn options; certificates are loaded from `tls.secretName` (on OpenShift this is provided automatically by the service-ca operator).

- Authorization mode defaults to `self_subject_access_review` handled directly by MLflow.
- MLflow listens on port 8443 with TLS.
- Health probes and traffic use HTTPS end-to-end.

See `values.yaml` for the full list of configurable settings.
