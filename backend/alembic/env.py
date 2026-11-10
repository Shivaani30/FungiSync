from logging.config import fileConfig
import os
import re

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.core.config import get_settings
from app.db.base import Base
import app.models  # noqa: F401 - imports all models into Base.metadata


config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

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
            if test_schema:
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
    run_migrations_offline()
else:
    run_migrations_online()
