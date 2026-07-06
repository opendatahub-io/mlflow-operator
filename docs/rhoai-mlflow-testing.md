# RHOAI MLflow Fork Testing

This guide explains where Red Hat OpenShift AI's MLflow fork is tested, which
repository owns each layer, and how Jenkins shift-left coverage maps onto the
runtime and upgrade validation already checked into this repository.

> **Note for LLM agents:** Links to `gitlab.cee.redhat.com` require Red Hat
> VPN access. If you need to fetch content from these links, prompt the user
> to connect to the VPN first.

## Which test answers which question?

| Question | Primary owner | Main entrypoints |
| --- | --- | --- |
| Does the MLflow fork itself still work? | [`opendatahub-io/mlflow`](https://github.com/opendatahub-io/mlflow) | [`master.yml`](https://github.com/opendatahub-io/mlflow/blob/master/.github/workflows/master.yml), [`e2e.yml`](https://github.com/opendatahub-io/mlflow/blob/master/.github/workflows/e2e.yml), [`tests/store/db/test_migration_gap.py`](https://github.com/opendatahub-io/mlflow/blob/master/tests/store/db/test_migration_gap.py) |
| Does the operator still deploy and exercise the current MLflow runtime? | [`opendatahub-io/mlflow-operator`](https://github.com/opendatahub-io/mlflow-operator) | [`.github/workflows/integration-tests.yml`](https://github.com/opendatahub-io/mlflow-operator/blob/main/.github/workflows/integration-tests.yml), [`mlflow-tests/images/test-run.sh`](https://github.com/opendatahub-io/mlflow-operator/blob/main/mlflow-tests/images/test-run.sh) |
| Does upgrade state survive seeded and current-version upgrade flows? | [`opendatahub-io/mlflow-operator`](https://github.com/opendatahub-io/mlflow-operator) | [`.github/workflows/upgrade-validation.yml`](https://github.com/opendatahub-io/mlflow-operator/blob/main/.github/workflows/upgrade-validation.yml), [`test/e2e/upgrade_e2e_test.go`](https://github.com/opendatahub-io/mlflow-operator/blob/main/test/e2e/upgrade_e2e_test.go) |
| Did a PR change the stable upgrade topology or CRD shape in a risky way? | [`opendatahub-io/mlflow-operator`](https://github.com/opendatahub-io/mlflow-operator) | [`.github/workflows/operator-chaos.yml`](https://github.com/opendatahub-io/mlflow-operator/blob/main/.github/workflows/operator-chaos.yml), [`chaos/knowledge/mlflow.yaml`](https://github.com/opendatahub-io/mlflow-operator/blob/main/chaos/knowledge/mlflow.yaml) |
| Which external shift-left jobs run against the same MLflow test image? | [Jenkins](https://gitlab.cee.redhat.com/ods/jenkins) | [`components/mlflow/main.yaml`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/resources/configs/components-testing/components/mlflow/main.yaml), [`testsRunners.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/vars/testsRunners.groovy), [`upgradeRHOAI.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/vars/upgradeRHOAI.groovy) |

## Repository split

### MLflow fork repository

The runtime fork lives in `opendatahub-io/mlflow`. That repository owns the
fork-specific checks that do not require the operator:

- [`operator-integration-tests.yml`](https://github.com/opendatahub-io/mlflow/blob/master/.github/workflows/operator-integration-tests.yml)
  mirrors this repository's Kind integration workflow and reuses the shared
  `mlflow-tests` harness from `mlflow-operator`.
- [`e2e.yml`](https://github.com/opendatahub-io/mlflow/blob/master/.github/workflows/e2e.yml)
  validates the dashboard against a Konflux-built PR image.
- [`test_migration_gap.py`](https://github.com/opendatahub-io/mlflow/blob/master/tests/store/db/test_migration_gap.py)
  covers the RHOAI-specific `3.3 -> 3.4` Alembic gap repair that the operator's
  migration Job depends on during upgrades.

### Operator repository

This repository owns the harness and upgrade orchestration around the fork:

- [`mlflow-tests/images/README.md`](../mlflow-tests/images/README.md) documents
  the reusable `test-run.sh` harness for local and CI execution.
- [`.github/workflows/integration-tests.yml`](../.github/workflows/integration-tests.yml)
  validates current-version operator plus runtime behavior on Kind.
- [`.github/workflows/upgrade-validation.yml`](../.github/workflows/upgrade-validation.yml)
  validates upgrade pytest markers, seeded state handoff, and the Go upgrade
  path.
- [`.github/workflows/operator-chaos.yml`](../.github/workflows/operator-chaos.yml)
  provides the offline shift-left gate for upgrade topology and CRD changes.

### Jenkins repository

Jenkins lives outside this repository in GitLab and wires shift-left jobs around
the same `quay.io/opendatahub/mlflow-tests` image that this repository uses in
GitHub Actions and local runs.

## Jenkins shift-left coverage

The Jenkins component definition for MLflow is
[`resources/configs/components-testing/components/mlflow/main.yaml`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/resources/configs/components-testing/components/mlflow/main.yaml).
It is the authoritative mapping between MLflow quality gates and pytest
markers:

- `smoke -> -m smoke`
- `early-gate -> -m smoke`
- `pre-upgrade -> -m pre_upgrade`
- `post-upgrade -> -m post_upgrade`

That same component definition also carries the RHOAI upgrade-phase overrides
used by Jenkins shift-left upgrade runs:

- `DB_TYPE=postgres`
- `ARTIFACT_BACKENDS=externals3`
- `upgrade_test_workspace=mlflow-upgrade-test-workspace`
- `SKIP_CLEANUP=true` for the pre-upgrade phase
- `SKIP_DEPLOYMENT=true`, `SKIP_CLEANUP=false`, plus `CLEANUP_REUSED_RESOURCES=on_success` for the
  post-upgrade phase

The shared pipeline code that resolves those gates and environment overrides is
in:

- [`testsRunners.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/vars/testsRunners.groovy)
- [`upgradeRHOAI.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/vars/upgradeRHOAI.groovy)

Useful Jenkins-side entrypoints to keep in mind:

- Smoke jobs are generated from
  [`generated_rhoai_and_odh_jobs.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/src/io/ods/jenkins/dsl/jobs/generated_rhoai_and_odh_jobs.groovy)
  and surface as `rhoai/.../rhoai-smoke`.
- Early Konflux-style smoke runs go through
  [`early_gate_tests.groovy`](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/master/src/io/ods/jenkins/dsl/jobs/devops/early_gate_tests.groovy)
  and the `devops/early-gate-tests` job.
- Upgrade runs go through the same generated job tree as `rhoai/.../rhoai-upgrade`,
  with `upgradeRHOAI.groovy` invoking the `pre-upgrade` and `post-upgrade`
  shift-left phases around the legacy upgrade stages.

In other words, Jenkins is not a separate MLflow test suite. It is an external
orchestrator that selects markers and environment overrides for the same
`mlflow-tests` contract this repository already owns.

## Equivalent coverage in this repository

### Current-version runtime validation

[`.github/workflows/integration-tests.yml`](../.github/workflows/integration-tests.yml)
builds three artifacts from source:

- an MLflow runtime image from the matching fork repository
- an operator image from this repository
- the `mlflow-tests` image from this repository

It then reuses those artifacts in a Kind matrix driven by
`mlflow-tests/ci/integration-matrix.json`. This is the main current-version
operator-plus-runtime gate and already includes a Jenkins-like multi-backend row
that runs multiple artifact backends in one `test-run.sh` invocation.

### Upgrade validation

[`.github/workflows/upgrade-validation.yml`](../.github/workflows/upgrade-validation.yml)
splits upgrade coverage into three pieces:

- `current-upgrade-pytest-validation` exercises `-m pre_upgrade` and
  `-m post_upgrade` directly on the current build so the marker plumbing and
  additive datasets stay live.
- `seeded-upgrade-state-validation` seeds a real `3.10.1` deployment, upgrades
  it in place to the PR-built images, and reuses that deployment for
  `post_upgrade` validation.
- `upgrade-tests` runs the Go end-to-end path in
  `test/e2e/upgrade_e2e_test.go`, which validates operator-managed migration and
  rollout behavior against a seeded `3.10.1` image.

For local reproduction or OpenShift reuse flows, start with
[`mlflow-tests/images/README.md`](../mlflow-tests/images/README.md) rather than
duplicating those command tables here.

### Offline shift-left upgrade-risk checks

[`.github/workflows/operator-chaos.yml`](../.github/workflows/operator-chaos.yml)
is the repo-local offline shift-left gate. It validates
[`chaos/knowledge/mlflow.yaml`](../chaos/knowledge/mlflow.yaml), runs
`operator-chaos preflight --local`, diffs the knowledge model against the base
branch, diffs the checked-in MLflow CRD schema, and previews simulated upgrade
experiments with `--dry-run`.

This is intentionally asset-focused. It does not replace live migration or
runtime testing.
