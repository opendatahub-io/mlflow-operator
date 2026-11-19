# Trace Analytics Prepopulation Job Workflow for OpenShift RHOAI

Platform administrators can backfill trace analytics before an OpenShift AI
(RHOAI) MLflow upgrade while MLflow keeps serving traffic. This Job is
administrator-run; the MLflow operator does not schedule or manage it.

## Background

`mlflow db prepopulate-trace-analytics` backfills denormalized columns ahead
of `mlflow db upgrade`, reducing writes during the upgrade window. The final
migration still scans and validates data, reconciles intervening writes,
removes legacy analytics data, and creates schema objects and indexes.
Prepopulation does not eliminate the upgrade window.

## When to run this workflow

Run it when all of the following are true:

- Your RHOAI MLflow deployment uses a Secret-backed PostgreSQL backend store
  (the primary database, not a read replica).
- The deployment is **live** and still serving traffic at the predecessor
  schema. The command validates the database's Alembic revision and refuses to
  run against anything else, including an already-upgraded database.
- You are preparing an upgrade to an MLflow release that contains the trace
  analytics migration.

The current command requires exactly Alembic revision `b7e2c1a4d9f3`, the
`down_revision` of trace analytics migration `75868b020152`. Upstream MLflow
3.16.0 has that schema head and is used as the CI predecessor fixture; this
does not establish that a supported RHOAI source release uses that revision.
Before production use, confirm the installed release's database revision
and the availability of a target image containing the command. Do not
deploy the CI image or manually stamp an older database to make it eligible.

Do **not** run it:

- Concurrently with `mlflow db upgrade` or an operator-managed migration Job,
  which scales MLflow to zero. Migration Jobs are labeled
  `mlflow.opendatahub.io/migration-job=true`.
- During peak traffic if you can avoid it. The command commits in small
  batches, but it still issues row updates against the primary database.
  Prefer a low-traffic window.

Always take a database backup before changing the database schema or contents.

## What the Job does and does not do

- It adds the denormalized analytics columns if they are missing and
  backfills their values from existing trace, span, and assessment data.
- It is **rerun-safe**: a rerun rescans from the beginning and skips rows
  whose values are already correct. See the rerun procedure below.
- It does **not** advance the Alembic revision. The final
  `mlflow db upgrade` during the operator-managed upgrade remains required
  and is the authoritative validation and cleanup step.
- The database URL is read from the `MLFLOW_TRACKING_URI` environment
  variable, sourced from a Secret, so credentials never appear in process
  arguments or shell history.

## Prerequisites

1. **Find the primary backend Secret.** Resolve the Secret the **live
   Deployment actually uses**, not just the CR field. Take the *last*
   `MLFLOW_BACKEND_STORE_URI` entry: later `spec.env` entries override generated
   `spec.backendStoreUriFrom` wiring.

   ```console
   $ kubectl get deployment mlflow -n redhat-ods-applications \
       -o jsonpath='{range .spec.template.spec.containers[?(@.name=="mlflow")].env[*]}{.name}{"\t"}{.valueFrom.secretKeyRef.name}{"\t"}{.valueFrom.secretKeyRef.key}{"\n"}{end}' \
       | awk '$1 == "MLFLOW_BACKEND_STORE_URI"' | tail -1
   MLFLOW_BACKEND_STORE_URI	mlflow-db-credentials	backend-store-uri
   ```

   Copy that Secret's name and key into the Job's `MLFLOW_TRACKING_URI`
   reference; never point it at a read replica. If the effective entry is
   inline or ConfigMap-backed instead of Secret-backed, stop and move the
   value into a Secret first. Only reference metadata is shown here — never
   print the URI itself. Keep `MLFLOW_READ_REPLICA_BACKEND_STORE_URI` empty in
   the Job so schema work targets the primary.

   The PostgreSQL role in this Secret must own the affected tables (or have
   equivalent privileges) so it can run the initial `ALTER TABLE ... ADD COLUMN`
   statements, as well as read and update their rows. Normal MLflow serving
   permissions alone may not be sufficient.

2. **Pick the Job image.** Use an image from your distribution channel that
   contains `mlflow db prepopulate-trace-analytics`, pinned by digest. Inspect
   the live Deployment's image as a starting point:

   ```console
   $ kubectl get deployment mlflow -n redhat-ods-applications \
       -o jsonpath='{.spec.template.spec.containers[?(@.name=="mlflow")].image}'
   registry.redhat.io/rhoai/odh-mlflow-rhel9@sha256:…
   ```

   The sample manifest's checked-in default is the OpenDataHub channel
   image (`quay.io/opendatahub/mlflow`); it is not rewritten per channel.
   On OpenShift AI, replace it with the appropriate
   `registry.redhat.io/rhoai/odh-mlflow-rhel9` image and configure its pull
   credentials before applying the Job.

   If the deployed image predates the command, choose a newer image from
   the same channel, not an arbitrary upstream build. Only the Job uses that
   image: leave the live server at the predecessor schema. Resolve image
   digests with `skopeo inspect docker://<image>` after logging in to the
   registry if required.

3. **Confirm no migration is in flight or about to start.** Check that the
   MLflow CR's `Migration` condition is `True` **and** that its
   `observedGeneration` equals the CR's current `metadata.generation`, and
   that the `mlflow.opendatahub.io/force-migrate` annotation is absent, and
   that no Job labeled `mlflow.opendatahub.io/migration-job=true` is running:

   ```console
   $ kubectl get mlflow <name> -o jsonpath='{.status.conditions[?(@.type=="Migration")].status}{"\n"}{.status.conditions[?(@.type=="Migration")].observedGeneration}{"\n"}{.metadata.generation}'
   True
   3
   3
   $ kubectl get jobs -n redhat-ods-applications -l mlflow.opendatahub.io/migration-job=true
   No resources found in redhat-ods-applications namespace.
   ```

   Check for a pending forced migration (even an empty annotation value
   triggers it); the following command must print `false`:

   ```console
   $ kubectl get mlflow <name> -o go-template='{{ $pending := false }}{{ range $key, $value := .metadata.annotations }}{{ if eq $key "mlflow.opendatahub.io/force-migrate" }}{{ $pending = true }}{{ end }}{{ end }}{{ $pending }}{{ "\n" }}'
   false
   ```

   If it prints `true`, wait for the operator to finish the forced migration
   and clear the annotation, then repeat all preflight checks.

   If the generations differ, wait for reconciliation and repeat the checks.
   With `migration.mode=Always`, a spec change can queue a migration while
   the condition still reports `True` for the previous generation.

   This preflight is only a snapshot. The operator has no awareness of the
   prepopulation Job. Until its pods terminate, hold:

   - **All MLflow CR spec changes**, including `spec.migration.mode`.
   - **Operator upgrades** and any other change that could restart or
     reconfigure the operator; a new supported version can trigger migration.
   - **Forced migrations**: do not add the
     `mlflow.opendatahub.io/force-migrate` annotation until prepopulation ends.

## Running the Job

Apply the sample manifest from this repository
(`config/samples/trace_analytics_prepopulation_job.yaml`) after replacing the
Secret reference, image, and namespace with your deployment's values.

If the Job image is private, configure the required `imagePullSecrets` in the
Job pod spec (reuse the live Deployment's when appropriate). If the Deployment
uses a custom database CA or client certificate, copy its database trust
environment (such as `PGSSLROOTCERT` and `PGSSLMODE`), the matching
Secret/ConfigMap mounts, and any CA-bundle init container into
the Job. For the operator's combined CA bundle, `PGSSLROOTCERT`,
`PGSSLMODE`, `combined-ca-bundle`, and `combine-ca-bundles` must refer to the
same certificate sources as the live Deployment. These fields must be present
**before** creating the Job; its pod template is immutable. If you already
created an incomplete Job, delete it, wait for its pods to terminate, and
apply the corrected manifest.

From the repository root:

```console
$ kubectl apply -f config/samples/trace_analytics_prepopulation_job.yaml
job.batch/mlflow-trace-analytics-prepopulation created
```

Key manifest fields:

| Field | Meaning |
| ----- | ------- |
| `spec.template.spec.containers[0].command/args` | Runs `mlflow db prepopulate-trace-analytics`; the URL comes from `MLFLOW_TRACKING_URI` |
| `--batch-size` | Rows committed per transaction, bounded 1–250 (default 250) to stay below supported-database parameter limits |
| `env[MLFLOW_TRACKING_URI]` | Secret reference to the **primary** backend store URI |
| `env[MLFLOW_READ_REPLICA_BACKEND_STORE_URI]` | Explicitly empty; never forward the read-replica Secret into this container |
| `containers[0].securityContext` | `readOnlyRootFilesystem: true` with a size-limited `tmp` `emptyDir` at `/tmp`, so the CLI cannot write elsewhere in the image |
| `spec.backoffLimit: 0` | Failures surface immediately; rerun deliberately instead of retrying in place |
| `spec.ttlSecondsAfterFinished: 86400` | Keeps the finished Job around for a day so logs stay inspectable |

Before rerunning (for example after an interruption), wait for the previous
Job's pods to terminate. A `kubectl wait` timeout does **not** stop the Job.
If it is still running, either let it finish or delete it and wait for pod
deletion before starting another run:

```console
$ kubectl delete job mlflow-trace-analytics-prepopulation -n redhat-ods-applications \
    --cascade=foreground --wait=true --timeout=5m
$ kubectl apply -f config/samples/trace_analytics_prepopulation_job.yaml
```

Foreground deletion waits for the Job's pods to be deleted. If deletion times
out, stop and investigate; do not start a replacement under a new name.
Once the old pods have terminated, rerun the Job. Never run two prepopulation
Jobs against the same database concurrently.

## Monitoring progress

The Job logs a stable progress line per entity while it scans, plus a summary
per entity (`Traces`, `Spans`, `Assessments`) and a completion line:

```console
$ kubectl logs -n redhat-ods-applications job/mlflow-trace-analytics-prepopulation -f
Prepopulating denormalized trace analytics columns...
Traces progress: scanned=250, updated=250
Traces: scanned=1904, updated=1904
Spans: scanned=5321, updated=5102
Assessments: scanned=118, updated=118
Prepopulation completed without advancing the Alembic revision. Run `mlflow db upgrade` during the final upgrade.
```

`scanned` counts every row the run examined; `updated` counts rows whose
values it actually changed. On a rerun, `updated` should be close to zero for
rows that existed before the first run.

## Verifying the run

```console
# Job completed successfully
$ kubectl wait --for=condition=Complete job/mlflow-trace-analytics-prepopulation \
    -n redhat-ods-applications --timeout=30m

# The Alembic revision is unchanged (still the predecessor revision)
$ kubectl exec -n <db-namespace> <postgres-pod> -- \
    psql -U <db-user> -d <db-name> -t -A -c 'SELECT version_num FROM alembic_version'
```

The revision must be unchanged before and after the run. If the command
rejects the database with a revision error, the deployment is not at the
expected predecessor schema — check which MLflow version is actually deployed
before proceeding.

## Sequencing with the upgrade

1. Run the prepopulation Job to completion (this workflow).
2. Verify the rerun is a near-no-op (`updated` counts near zero for pre-existing
   rows) if you want extra confidence before the upgrade window.
3. Proceed with the normal operator-managed upgrade. The operator scales the
   MLflow Deployment to zero, runs its migration Job (`mlflow db upgrade`),
   and scales the Deployment back up.
4. Confirm the upgrade completed: the MLflow CR reports the new version in
   `status.version` and its `Migration` condition is `True`.

## Troubleshooting

- **Job fails with a revision validation error**: the database is not at the
  predecessor revision the command requires. Either the deployment was
  already upgraded, or it predates the supported predecessor line. Do not
  attempt to edit the Alembic revision manually.
- **Job fails with a database operation error**: the CLI keeps the DSN out of
  the message on purpose. Check whether the Secret's URI is reachable from
  the cluster, whether the credentials are current, and whether long-running
  transactions are blocking the initial column additions; retry during a
  low-traffic period.
- **Job pod stays pending**: the Job sets `runAsNonRoot` and drops all
  capabilities; confirm the image runs as a non-root user and that resource
  limits fit the node.
- **Job pod is stuck in `ImagePullBackOff`**: the image reference is not
  pullable from this cluster. The sample's default is the OpenDataHub
  channel image; on OpenShift AI use the live Deployment's
  `registry.redhat.io/rhoai/odh-mlflow-rhel9` image and copy the live
  Deployment's `imagePullSecrets` into the Job pod spec.

## Test coverage

The opt-in `trace_analytics_prepopulation` test phase covers live backfill and
rerun idempotency, followed by the final upgrade in CI. See the
[testing guide](rhoai-mlflow-testing.md#trace-analytics-prepopulation-validation)
for setup and coverage limits.
