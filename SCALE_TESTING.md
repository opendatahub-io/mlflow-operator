# OpenShift SQL trace rollup scale testing

This is a local testing branch, not a production rollout. No branches or images
have been published. Phase 3 implements RHOAIENG-78203's standalone rollup scheduler.

## Coordinated sources and images

All three repositories use local branch `db-optimization/mlflow-openshift-scale-testing`:

- Kubernetes plugins: `8e1d9c6fbc063a137326d17e6f16dc3d5d59a7ed`, based on
  `kubeflow/main` at `050fbec05cdd9ec5af17f87e72551aa82881c663`.
- MLflow: `9efac73528cc61cb3d736bd39081505711dab90b`, includes upstream
  `34c75beb57e0cecf3556df2ebc5f29689cf2e15c` and ODH base
  `a721e22361b5161ee45d1639372d82c2cfbd5c59`.
- Operator: based on fetched `odh/main` at
  `a29587217b3df52bb31a45797e0bdfbe4910c767`.
  The final local Operator commit is recorded in the companion handoff.

The Phase 2 runtime is `localhost/mlflow:openshift-scale-testing-34c75beb`, local
Podman image ID `sha256:9f0e60377d5b6c9a5a6bd234680f4a9792186fa9fd990f00def1fc37a60c5123`.
It contains MLflow `3.16.2.dev0` and the Phase 1 plugin wheel (package version still
`1.6.0`, SHA256 `8217520438f1849d7b0602ef7fd4944fa7fe99ff30f63a9e369d00125e5d17ff`).
MLflow's `SCALE_TESTING.md` explains the local wheel override and the pinned Git
wheel source used after publication. Remote Git builds cannot access that commit yet.

Operator metadata and both default runtime `params.env` files target this local
runtime. Migration checks the exact `3.16.2.dev0` version. Historical sample image
pins are illustrative; use `config/samples/mlflow_v1_mlflow_trace_rollups.yaml` for
this coordinated test. Never deploy this Operator with an older runtime image.

## Build and prepare

From the Operator feature checkout:

```sh
make manifests generate
make build
make docker-build CONTAINER_TOOL=podman IMG=localhost/mlflow-operator:openshift-scale-testing
```

For an OpenShift test, provide images accessible to every node. Publication needs
the user's direction; alternatively use an explicitly chosen local image-loading
mechanism. A `localhost/...` image in workstation Podman storage is not available
to cluster nodes. Replace the Operator overlay image and the sample CR runtime
image with the approved registry references, preferably digests. Set the Operator's
`MLFLOW_IMAGE` to that same runtime image, and configure the real external
`MLFLOW_URL` and Gateway before applying the selected OpenShift overlay.

Create database and S3 connection Secrets in the operand namespace. The sample
expects `mlflow-db-credentials` key `backend-store-uri`, and `mlflow-s3-credentials`
with the AWS/MLflow object-store environment variables. Use a dedicated test database
and bucket. Optional read replicas must already have compatible schema and replicate
the rollup tables; every maintenance pass uses the primary.

Apply the scale-testing CR after the Operator is installed. Operator-managed
migration initializes/upgrades schema before server rollout and rollup scheduling.
Optionally install `config/admission` on Kubernetes 1.30+ / an OpenShift version
serving the v1 ValidatingAdmissionPolicy API. It emits timing warnings without
blocking resource creation; it is not included in base installation.

## Run a maintenance pass

The sample explicitly schedules nightly at 02:00 UTC. Change `schedule` and
`timeZone` independently for the testing timezone. PostgreSQL/MySQL default to
scheduling enabled, even if the entire `traceRollups` block is absent; SQLite
never schedules through the Operator. Explicit `enabled: false` deletes the
CronJob but leaves rollup reads and source-mutation invalidation enabled.

For an immediate pass after migration and availability are complete:

```sh
oc -n redhat-ods-applications create job --from=cronjob/mlflow-trace-rollups mlflow-trace-rollups-manual
oc -n redhat-ods-applications logs -f job/mlflow-trace-rollups-manual
oc -n redhat-ods-applications wait --for=condition=complete job/mlflow-trace-rollups-manual --timeout=30m
```

Confirm no scheduled pass is active first: `Forbid` coordinates scheduled jobs,
not arbitrary manual jobs. Manual jobs inherit the instance label used by migration
quiescence. Delete finished manual jobs when no longer needed. Disable scheduling
before intentionally leaving a manual job pending, otherwise migrations wait for it.

Tune `MLFLOW_TRACE_ROLLUPS_MAX_PARTITIONS_PER_RUN` (default 1000) and
`MLFLOW_TRACE_ROLLUPS_MAX_WORKERS` (default 4) via `spec.env`; job resources are
independent of tracking replicas. Finished inactive historical days become eligible
according to MLflow's 24-hour lag. Bootstrap may need multiple passes. Inspect the
per-family built/deferred/failed/skipped-cap logs and compare eligible trace analytics
against the raw-query path in an isolated client. Raw data remains authoritative.

The job receives CA trust, env/envFrom, placement/security settings, and main
ServiceAccount workload identity, with no Kubernetes token or PVC. It invokes
`run_sql_trace_rollup_scheduler` directly with the primary SQL engine and propagates
failed partitions as failure. The MLflow jobs backend stays disabled. Explicit
`MLFLOW_SQL_TRACE_ROLLUPS_ENABLED=false` skips maintenance; after materialization,
turning that MLflow feature off requires stopping all database-connected servers,
using `mlflow db delete-trace-rollups`, and restarting, as documented upstream.
Use the scheduling opt-out when the aim is only to stop background work.

## Validation and limits

Validation logs and executable runtime checks are in the companion
`ai_assist_files/db-optimization/phase-3-validation/` directory; the handoff records
final results. Local checks cover generation, rendering, API admission/warnings,
Secret-backed SQLite, cleanup, migration suspension, and execution against local
PostgreSQL with the Phase 2 image. No live OpenShift deployment, RBAC/scale test,
remote GitHub CI, registry publication, other architecture, MySQL runtime, or
hermetic Konflux Git prefetch has been performed. Standalone Helm users must
coordinate schema migration and maintenance themselves.
