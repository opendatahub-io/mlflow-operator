#!/bin/bash
# prepopulation-eligibility.sh: eligibility gate for the trace analytics
# prepopulation pytest phase.
#
# Prints "eligible" when the current suite deploys both MLflow and a
# self-managed PostgreSQL backend and an explicit Job image was provided;
# otherwise prints "ineligible: <reason>". test-run.sh exports the phase flag
# from the result. Kept as a standalone script so the gate is unit-testable
# (see mlflow-tests/tests/test_trace_prepopulation_actions.py).
#
# The backend comparison accepts both `postgres` and `postgresql` spellings,
# mirroring the deploy path in test-run.sh; matching only one spelling would
# silently skip the phase for an eligible suite.

set -euo pipefail

if [ -z "${TRACE_PREPOPULATION_IMAGE:-}" ]; then
    echo "ineligible: TRACE_PREPOPULATION_IMAGE not set"
    exit 0
fi
if [ "${SKIP_DEPLOYMENT:-false}" = "true" ]; then
    echo "ineligible: SKIP_DEPLOYMENT=true reuses an existing deployment"
    exit 0
fi
if [ "${SKIP_INFRASTRUCTURE:-false}" = "true" ]; then
    echo "ineligible: SKIP_INFRASTRUCTURE=true reuses cluster infrastructure"
    exit 0
fi
case "${BACKEND_STORE:-}" in
    postgres|postgresql) ;;
    *)
        echo "ineligible: BACKEND_STORE=${BACKEND_STORE:-<unset>} is not self-deployed PostgreSQL"
        exit 0
        ;;
esac
if [ -n "${DB_HOST:-}" ]; then
    echo "ineligible: explicit DB_HOST targets an external database"
    exit 0
fi
echo "eligible"
