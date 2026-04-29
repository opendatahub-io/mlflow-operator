import os

import mlflow.store.db.utils as db_utils
from mlflow.version import VERSION

try:
    from mlflow.store.db.migration_gap import fix_migration_gap_if_needed
except ImportError:
    fix_migration_gap_if_needed = None


def supports_sql_migration(uri):
    dialect = uri.split(":", 1)[0].split("+", 1)[0]
    return dialect in ("sqlite", "postgresql", "mysql")


def maybe_fix_backend_migration_gap(name, engine):
    if name != "backend" or fix_migration_gap_if_needed is None:
        return

    print("Checking backend store for the RHOAI 3.3 -> 3.4 migration gap")
    fix_migration_gap_if_needed(engine)


def migrate_store(name, uri):
    engine = db_utils.create_sqlalchemy_engine_with_retry(uri)
    maybe_fix_backend_migration_gap(name, engine)
    current_rev = db_utils._get_schema_version(engine)
    print(f"{name} store current revision: {current_rev!r}")
    if not current_rev:
        print(f"{name} store has no Alembic revision; bootstrapping schema")
        db_utils._initialize_tables(engine)
    else:
        print(f"{name} store upgrading from revision {current_rev!r}")
        db_utils._upgrade_db(engine)

    final_rev = db_utils._get_schema_version(engine)
    latest_rev = db_utils._get_latest_schema_revision()
    if final_rev != latest_rev:
        raise RuntimeError(
            f"{name} store schema revision {final_rev!r} does not match head {latest_rev!r}"
        )
    print(f"{name} store migrated to revision {final_rev!r}")


backend_uri = os.environ.get("MLFLOW_BACKEND_STORE_URI", "").strip()
registry_uri = os.environ.get("MLFLOW_REGISTRY_STORE_URI", "").strip()

stores = []
if backend_uri and supports_sql_migration(backend_uri):
    stores.append(("backend", backend_uri))
elif backend_uri:
    print(f"Skipping backend store migration for non-SQL URI: {backend_uri}")
if registry_uri and registry_uri != backend_uri and supports_sql_migration(registry_uri):
    stores.append(("registry", registry_uri))
elif registry_uri and registry_uri != backend_uri:
    print(f"Skipping registry store migration for non-SQL URI: {registry_uri}")

if not stores:
    print("No SQL backend or registry stores require migration")
    raise SystemExit(0)

print(f"Running migration with MLflow {VERSION}")
for name, uri in stores:
    migrate_store(name, uri)
