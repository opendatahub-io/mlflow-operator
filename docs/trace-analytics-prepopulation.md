# Trace analytics prepopulation on OpenShift RHOAI

This is the operator-repository Job workflow for large MLflow SQL trace stores. It runs
`mlflow db prepopulate-trace-analytics` against the **primary backend database** while
the existing MLflow Deployment serves traffic. The Job does not advance the Alembic
revision or remove legacy analytics data. The operator-managed `mlflow db upgrade`
remains required during the later MLflow version upgrade. This workflow is separate
from the broader SQL analytics rollout guidance tracked in RHOAIENG-78204.
The [upstream backend-store guide](https://github.com/mlflow/mlflow/blob/master/docs/docs/self-hosting/architecture/backend-store.mdx)
defines the predecessor revision and final upgrade sequence.

## Before running

1. Take and verify a restorable backup of the primary database. Choose a low-traffic
   window; prepopulation writes in batches and can increase database load.
2. Confirm the running `MLflow` resource has a remote SQL backend and its primary
   database is at the immediate predecessor Alembic revision `b7e2c1a4d9f3`.
   The command rejects other revisions. For a Secret-backed backend, inspect
   `spec.backendStoreUriFrom` and the effective `MLFLOW_BACKEND_STORE_URI` entry on
   the live `mlflow` Deployment. They must reference the same primary Secret name
   and key. `spec.env` can override the CR's backend-store reference; resolve that
   mismatch before proceeding. Do not print the Secret value.
   Query the revision from a running MLflow pod, which already has the backend
   URI and database trust configuration in its environment:

   ```bash
   oc -n redhat-ods-applications exec deploy/mlflow -c mlflow -- \
     python3 -c 'import os; from sqlalchemy import create_engine, text; e = create_engine(os.environ["MLFLOW_BACKEND_STORE_URI"]); print(e.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one())'
   ```

   The output must be `b7e2c1a4d9f3`. Run the same read-only query after the
   Job to confirm that prepopulation did not change the revision.
3. Choose an MLflow image containing `mlflow db prepopulate-trace-analytics`. Keep
   the running Deployment on its current image and leave the `MLflow` CR unchanged
   until prepopulation finishes. The Job may use a newer image because it runs only
   the database command. Pin an immutable image digest and confirm the image
   supports the target database driver.
4. Verify the MLflow CR is steady: its `Migration` condition has completed for
   the current generation, the Deployment is available, and no operator upgrade,
   force-migrate request, or CR/image change is planned until the Job finishes.
   Check that no operator migration Job is pending or running:

   ```bash
   oc -n redhat-ods-applications get jobs -l mlflow.opendatahub.io/migration-job=true
   oc -n redhat-ods-applications get deployment mlflow
   ```

   Do not start prepopulation during `mlflow db upgrade`, an operator migration, or
   another prepopulation Job. The sample is an administrator-run one-shot Job; the
   operator does not coordinate it with its own migration Jobs.

## Run the Job

Copy [the sample Job](../config/samples/trace_analytics_prepopulation_job.yaml) to a
local file. Set `metadata.namespace` to the namespace containing the MLflow Deployment
(`redhat-ods-applications` in RHOAI). Replace `REPLACE_WITH_PREPOPULATION_IMAGE` with
the chosen image reference. Set the `MLFLOW_TRACKING_URI` Secret name and key to the
same primary backend URI referenced by the live Deployment's effective
`MLFLOW_BACKEND_STORE_URI`. The URI stays in
the Secret and is never passed as a command argument. Do not use a read-replica URI.
If the image is private, copy the Deployment's `imagePullSecrets` into the Job pod spec.

If the Deployment uses a custom database CA or client certificate, copy its database
trust environment, Secret/ConfigMap mounts, and any CA-bundle init container into the
Job. For the operator's combined CA bundle, `PGSSLROOTCERT`, `PGSSLMODE`,
`combined-ca-bundle`, and `combine-ca-bundles` must refer to the same certificate
sources. Check the resulting manifest before applying it. The Job has no Kubernetes
API need, so it does not mount a service-account token.

```bash
oc apply -f trace_analytics_prepopulation_job.yaml
oc -n redhat-ods-applications logs -f job/mlflow-trace-analytics-prepopulation
oc -n redhat-ods-applications wait --for=condition=complete \
  job/mlflow-trace-analytics-prepopulation --timeout=1800s
```

The logs report `scanned` and `updated` counts for traces, spans, and assessments.
On failure, inspect the Job and pod events and logs with `oc describe job` and
`oc logs`. Do not start the final upgrade until the Job has completed successfully.
The sample uses `backoffLimit: 0` to expose a failure for operator review and keeps
the finished Job for 24 hours unless removed earlier.

If interrupted or failed, correct the cause, wait for the old Job to be deleted,
then apply the same manifest again. The command rescans and skips rows already
correct; a successful rerun is safe. A completed Job can be rerun the same way:

```bash
oc -n redhat-ods-applications delete job mlflow-trace-analytics-prepopulation --wait=true
oc apply -f trace_analytics_prepopulation_job.yaml
```

## Final upgrade

After the Job completes, follow the planned RHOAI MLflow upgrade. Stop all MLflow
writers and verify the database backup before the final schema migration. The
operator scales its metadata-connected Deployments down and runs `mlflow db upgrade`
as part of its migration flow; wait for the `Migration` condition to succeed and
the Deployment to become available before restoring traffic. Do not run the
prepopulation Job during this phase or mix old and new MLflow server versions.
Prepopulation alone is not an upgrade and leaves the Alembic revision unchanged.

## Integration validation

`mlflow-tests/tests/test_trace_analytics_prepopulation.py` runs the sample Job twice
against an operator-managed, Secret-backed MLflow Deployment at the predecessor
revision on PostgreSQL. A read-only probe Job checks Alembic, the seeded trace's
analytics value, and its PostgreSQL row version before, between, and after both
runs. The unchanged row version on the second pass verifies the completed row
was skipped, even if other writers create new rows. The test checks the primary
Secret reference, Job progress and success, repeat execution, live MLflow API
reachability during each Job, Deployment availability, and the absence of
overlapping operator migration Jobs. Run it on a dedicated test cluster because
the integration harness creates workspace namespaces and RBAC. Use a known-good
older MLflow deployment at the predecessor schema and the candidate command
image, before changing the `MLflow`
CR image:

```bash
MLFLOW_PREPOPULATION_IMAGE=<candidate-image> \
PYTEST_MARK_EXPRESSION=prepopulation \
SKIP_DEPLOYMENT=true SKIP_OPERATOR=true SKIP_INFRASTRUCTURE=true SKIP_CLEANUP=true \
ARTIFACT_BACKENDS=s3 BACKEND_STORE=postgres REGISTRY_STORE=postgres \
mlflow-tests/images/run-integration-tests.sh
```

Set `ARTIFACT_BACKENDS` to the storage type of the reused deployment. Set the
remaining harness environment (test image, namespace, credentials, and
tracking endpoint) for that deployment as described in
[the integration test README](../mlflow-tests/README.md). The test image includes
the sample manifest and reads it directly. The test does not perform the final
schema upgrade; use the separate upgrade e2e path to validate the operator's
scale-down and migration behavior.
