"""Run one SQL trace rollup pass on the primary, without MLflow job execution."""
import logging
import os
from types import SimpleNamespace

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
try:
    stats = run_sql_trace_rollup_scheduler(SimpleNamespace(engine=engine))
    if stats is None:
        raise RuntimeError("SQL trace rollup maintenance unexpectedly skipped")
    if any(getattr(stats, family).failed for family in ("trace_metric", "span_cost", "assessment")):
        raise RuntimeError("SQL trace rollup maintenance reported failed partitions")
finally:
    engine.dispose()
