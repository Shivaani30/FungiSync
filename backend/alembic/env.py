from logging.config import fileConfig
import os
import re

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

from app.db.base import Base
import app.models  # noqa: F401 - imports all models into Base.metadata


config = context.config
migration_url = os.environ.get("MIGRATION_DATABASE_URL")
if not migration_url:
    raise RuntimeError("MIGRATION_DATABASE_URL is required; Alembic does not use APP_DATABASE_URL")
if make_url(migration_url).username != "fungisync_migration_admin":
    raise RuntimeError("MIGRATION_DATABASE_URL must use fungisync_migration_admin")
config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
test_schema = os.getenv("FUNGISYNC_ALEMBIC_SCHEMA")
if test_schema and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", test_schema):
    raise RuntimeError("FUNGISYNC_ALEMBIC_SCHEMA must be a simple PostgreSQL identifier")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    test_target = None
    if test_schema:
        from tests.db_safety import load_test_target

        test_target = load_test_target()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=test_schema,
        )
        with context.begin_transaction():
            if test_target is not None:
                from tests.db_safety import verify_admin_connection

                verify_admin_connection(connection, test_target)
                connection.execute(text(f'SET search_path TO "{test_schema}"'))
            diagnostic = connection.execute(text("""
                SELECT current_database(), current_schema(), current_schemas(true), current_setting('search_path')
            """)).one()
            database, schema, schemas, search_path = diagnostic
            config.print_stdout(
                f"Alembic connection: database={database} schema={schema} "
                f"schemas={schemas} search_path={search_path}"
            )
            context.run_migrations()


if context.is_offline_mode():
    if test_schema:
        raise RuntimeError("Offline Alembic is disabled for guarded test migrations")
    run_migrations_offline()
else:
    run_migrations_online()
