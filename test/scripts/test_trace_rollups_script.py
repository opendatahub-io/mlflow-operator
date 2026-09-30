"""Wrapper regression tests; run with the matching MLflow runtime installed."""

import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "charts/mlflow/scripts/trace_rollups.py"
FLAG = "MLFLOW_SQL_TRACE_ROLLUPS_ENABLED"


class TraceRollupsScriptTest(unittest.TestCase):
    def test_explicit_disable_skips_database_and_scheduler(self):
        for value in ("false", "FALSE", "0"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {
                    "MLFLOW_BACKEND_STORE_URI": "postgresql://primary/mlflow",
                    FLAG: value,
                    "MLFLOW_TRACE_ROLLUPS_MAX_PARTITIONS_PER_RUN": "0",
                    "MLFLOW_TRACE_ROLLUPS_MAX_WORKERS": "0",
                }),
                patch("mlflow.store.db.utils.create_sqlalchemy_engine_with_retry") as create_engine,
                patch("mlflow.tracing.trace_rollup_service.run_sql_trace_rollup_scheduler") as scheduler,
            ):
                with self.assertRaises(SystemExit) as exit_info:
                    runpy.run_path(str(SCRIPT), run_name="__main__")
                self.assertEqual(exit_info.exception.code, 0)
                self.assertEqual(os.environ[FLAG], value)
                create_engine.assert_not_called()
                scheduler.assert_not_called()

    def test_default_and_explicit_enable_use_primary_and_dispose_engine(self):
        dialects = (
            ("postgresql://primary/mlflow", "SELECT pg_try_advisory_lock(78203, 1)", "SELECT pg_advisory_unlock(78203, 1)"),
            (
                "mysql+pymysql://primary/mlflow",
                "SELECT GET_LOCK('mlflow-operator-trace-rollups-migration', 10)",
                "SELECT RELEASE_LOCK('mlflow-operator-trace-rollups-migration')",
            ),
        )
        for uri, acquire_sql, release_sql in dialects:
            for value in (None, "true", "1"):
                with (
                    self.subTest(uri=uri, value=value),
                    patch.dict(os.environ, {
                        "MLFLOW_BACKEND_STORE_URI": uri,
                        "MLFLOW_READ_REPLICA_BACKEND_STORE_URI": "postgresql://replica/mlflow",
                        "MLFLOW_TRACE_ROLLUPS_MAX_PARTITIONS_PER_RUN": "1",
                        "MLFLOW_TRACE_ROLLUPS_MAX_WORKERS": "1",
                    }),
                    patch("mlflow.store.db.utils.create_sqlalchemy_engine_with_retry") as create_engine,
                    patch("mlflow.tracing.trace_rollup_service.run_sql_trace_rollup_scheduler") as scheduler,
                ):
                    if value is None:
                        os.environ.pop(FLAG, None)
                    else:
                        os.environ[FLAG] = value
                    scheduler.return_value = SimpleNamespace(
                        **{family: SimpleNamespace(failed=0)
                           for family in ("trace_metric", "span_cost", "assessment")}
                    )
                    runpy.run_path(str(SCRIPT), run_name="__main__")
                    self.assertEqual(os.environ[FLAG], value or "true")
                    self.assertEqual(os.environ["MLFLOW_READ_REPLICA_BACKEND_STORE_URI"], "")
                    create_engine.assert_called_once_with(uri)
                    scheduler.assert_called_once()
                    self.assertIs(scheduler.call_args.args[0].engine, create_engine.return_value)
                    connection = create_engine.return_value.connect.return_value
                    self.assertEqual(connection.execution_options.call_args.kwargs, {"isolation_level": "AUTOCOMMIT"})
                    self.assertEqual(connection.execute.call_args_list[0].args[0].text, acquire_sql)
                    self.assertEqual(connection.execute.call_args_list[-1].args[0].text, release_sql)
                    connection.close.assert_called_once_with()
                    create_engine.return_value.dispose.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
