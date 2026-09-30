"""Run one SQL trace rollup pass on the primary, without MLflow job execution."""
import logging
import os
import time
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.engine import make_url

logging.basicConfig(level=logging.INFO)
uri = os.environ["MLFLOW_BACKEND_STORE_URI"]
dialect = make_url(uri).get_backend_name()
if dialect == "sqlite":
    logging.info("Skipping SQL trace rollup maintenance for SQLite")
    raise SystemExit(0)
if dialect not in {"postgresql", "mysql"}:
    raise RuntimeError("SQL trace rollups require a PostgreSQL or MySQL primary backend")

# Scheduling is independent of the server jobs backend. Never route maintenance
# through a read replica. The rendered default can be explicitly overridden.
os.environ.setdefault("MLFLOW_SQL_TRACE_ROLLUPS_ENABLED", "true")
os.environ["MLFLOW_READ_REPLICA_BACKEND_STORE_URI"] = ""
from mlflow.store.db.utils import create_sqlalchemy_engine_with_retry
from mlflow.tracing.trace_rollup_service import run_sql_trace_rollup_scheduler
from mlflow.environment_variables import (
    MLFLOW_SQL_TRACE_ROLLUPS_ENABLED,
    MLFLOW_TRACE_ROLLUPS_MAX_PARTITIONS_PER_RUN,
    MLFLOW_TRACE_ROLLUPS_MAX_WORKERS,
)

if not MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.get():
    logging.info("Skipping maintenance because SQL trace rollups are explicitly disabled")
    raise SystemExit(0)

for variable in (MLFLOW_TRACE_ROLLUPS_MAX_PARTITIONS_PER_RUN, MLFLOW_TRACE_ROLLUPS_MAX_WORKERS):
    if variable.get() < 1:
        raise ValueError(f"{variable.name} must be positive")

engine = create_sqlalchemy_engine_with_retry(uri)
lock_connection = None
lock_acquired = False
try:
    # Session-level advisory locks coordinate with the operator migration Job.
    # Keep this connection open while the scheduler uses its own pooled connections.
    lock_connection = engine.connect()
    lock_connection.execution_options(isolation_level="AUTOCOMMIT")
    if dialect == "postgresql":
        acquire_lock = text("SELECT pg_try_advisory_lock(78203, 1)")
        release_lock = text("SELECT pg_advisory_unlock(78203, 1)")
    else:
        acquire_lock = text("SELECT GET_LOCK('mlflow-operator-trace-rollups-migration', 10)")
        release_lock = text("SELECT RELEASE_LOCK('mlflow-operator-trace-rollups-migration')")
    while not lock_connection.execute(acquire_lock).scalar():
        logging.info("Waiting for another SQL trace rollup or migration operation to finish")
        time.sleep(5)
    lock_acquired = True

    stats = run_sql_trace_rollup_scheduler(SimpleNamespace(engine=engine))
    if stats is None:
        raise RuntimeError("SQL trace rollup maintenance unexpectedly skipped")
    if any(getattr(stats, family).failed for family in ("trace_metric", "span_cost", "assessment")):
        raise RuntimeError("SQL trace rollup maintenance reported failed partitions")
finally:
    try:
        if lock_acquired:
            try:
                lock_connection.execute(release_lock)
            finally:
                lock_connection.close()
        elif lock_connection is not None:
            lock_connection.close()
    finally:
        engine.dispose()
