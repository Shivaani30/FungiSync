import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


BACKEND_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
PRE_ALLOCATION_REVISION = "3585205556e4"
MIGRATION_TEST_SCHEMA = "fungisync_migration_test"


def _test_database_url() -> str:
    url = get_settings().test_database_url
    if not url or make_url(url).database != "fungisync_test_db":
        raise RuntimeError("Migration tests require TEST_DATABASE_URL for fungisync_test_db.")
    return url


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = _test_database_url()
    environment["TEST_DATABASE_URL"] = _test_database_url()
    environment["FUNGISYNC_ALEMBIC_SCHEMA"] = MIGRATION_TEST_SCHEMA
    return environment


def _catalog_snapshot(engine) -> dict[str, object]:
    """Read-only catalog evidence for the protected migration-test database."""
    with engine.connect() as connection:
        context = connection.execute(text("""
            SELECT current_database(), current_schema(), current_schemas(true), current_setting('search_path')
        """)).one()
        if context[0] != "fungisync_test_db":
            raise RuntimeError("Migration catalog inspection refused a non-test database.")
        schemas = connection.execute(text("""
            SELECT nspname FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
            ORDER BY nspname
        """)).scalars().all()
        tables = connection.execute(text("""
            SELECT schemaname, tablename FROM pg_tables
            WHERE schemaname NOT LIKE 'pg_%' AND schemaname <> 'information_schema'
            ORDER BY schemaname, tablename
        """)).all()
        relations = connection.execute(text("""
            SELECT n.nspname, c.relname, c.relkind
            FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
            ORDER BY n.nspname, c.relname
        """)).all()
        version_schemas = connection.execute(text("""
            SELECT schemaname FROM pg_tables WHERE tablename = 'alembic_version' ORDER BY schemaname
        """)).scalars().all()
        versions = {}
        for schema in version_schemas:
            quoted_schema = '"' + schema.replace('"', '""') + '"'
            versions[schema] = connection.execute(text(f"SELECT version_num FROM {quoted_schema}.alembic_version")).scalars().all()
    return {
        "database": context[0], "schema": context[1], "schemas": list(context[2]),
        "search_path": context[3], "namespaces": schemas, "tables": tables,
        "relations": relations, "alembic_versions": versions,
    }


def _alembic(engine, *arguments: str) -> dict[str, object]:
    command = [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments]
    result = subprocess.run(
        command,
        cwd=BACKEND_DIR,
        env=_subprocess_environment(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "Alembic subprocess failed\n"
            f"command: {command!r}\nreturn code: {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return _catalog_snapshot(engine)


def _subprocess_catalog_context() -> tuple[str, str, str, int]:
    script = """
from sqlalchemy import create_engine, text
from app.core.config import get_settings
schema = __import__('os').environ['FUNGISYNC_ALEMBIC_SCHEMA']
with create_engine(get_settings().database_url, connect_args={'options': f'-csearch_path={schema}'}).connect() as connection:
    row = connection.execute(text(\"\"\"
        SELECT current_database(), current_schema(), current_setting('search_path'),
               (SELECT count(*)
                FROM pg_type JOIN pg_namespace ON pg_namespace.oid = pg_type.typnamespace
                WHERE nspname = current_schema()
                  AND typname IN ('batch_status', 'order_status'))
    \"\"\")).one()
    print('|'.join(map(str, row)))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env=_subprocess_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    database, schema, search_path, enum_count = result.stdout.strip().split("|", maxsplit=3)
    return database, schema, search_path, int(enum_count)


def test_allocation_migration_preserves_populated_current_schema() -> None:
    """Exercise upgrade/downgrade only against the explicitly protected test database."""
    test_url = _test_database_url()
    admin_engine = create_engine(test_url)
    engine = create_engine(test_url, connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})
    grower_id, buyer_id, batch_id, order_id = (uuid4() for _ in range(4))

    # Isolate the migration chain from metadata fixtures, which use public.
    with admin_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {MIGRATION_TEST_SCHEMA}"))
    with engine.connect() as connection:
        reset_context = connection.execute(text("""
            SELECT current_database(), current_schema(), current_setting('search_path'),
                   (SELECT count(*)
                    FROM pg_type JOIN pg_namespace ON pg_namespace.oid = pg_type.typnamespace
                    WHERE nspname = current_schema()
                      AND typname IN ('batch_status', 'order_status'))
        """)).one()
    assert reset_context[0] == make_url(test_url).database
    assert reset_context[1] == MIGRATION_TEST_SCHEMA
    assert reset_context[3] == 0
    assert _subprocess_catalog_context() == tuple(reset_context)
    _alembic(engine, "stamp", "base")
    empty_upgrade_snapshot = _alembic(engine, "upgrade", "head")  # Empty-database upgrade.
    inspector = inspect(engine)
    assert {
        "allocation_proposals",
        "allocation_proposal_versions",
        "allocation_lines",
        "allocation_events",
    }.issubset(inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA)), empty_upgrade_snapshot

    _alembic(engine, "downgrade", PRE_ALLOCATION_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO growers (id, name, phone, location) VALUES (:id, :name, :phone, :location)"),
            {"id": grower_id, "name": "Legacy grower", "phone": "9999999999", "location": "Pune"},
        )
        connection.execute(
            text("INSERT INTO buyers (id, name, location, contact) VALUES (:id, :name, :location, :contact)"),
            {"id": buyer_id, "name": "Legacy buyer", "location": "Pune", "contact": "buyer@example.invalid"},
        )
        connection.execute(
            text("""INSERT INTO batches
                (id, grower_id, species, expected_kg, confirmed_kg, harvest_date, status, asking_price_per_kg, quality_grade, best_before_date)
                VALUES (:id, :grower_id, 'oyster', 3.000, 2.000, DATE '2026-11-10', 'confirmed', 90.00, 'Grade A', DATE '2026-11-12')"""),
            {"id": batch_id, "grower_id": grower_id},
        )
        connection.execute(
            text("""INSERT INTO orders
                (id, buyer_id, species, quantity_kg, price_limit, delivery_date, status, required_quality_grade)
                VALUES (:id, :buyer_id, 'oyster', 2.000, 100.00, DATE '2026-11-11', 'pending', 'Export Quality')"""),
            {"id": order_id, "buyer_id": buyer_id},
        )

    populated_upgrade_snapshot = _alembic(engine, "upgrade", "head")
    inspector = inspect(engine)
    assert "allocation_quality_grade" in {column["name"] for column in inspector.get_columns("batches", schema=MIGRATION_TEST_SCHEMA)}
    assert "allocation_required_quality_grade" in {column["name"] for column in inspector.get_columns("orders", schema=MIGRATION_TEST_SCHEMA)}
    assert {"allocation_proposals", "allocation_proposal_versions", "allocation_lines", "allocation_events"}.issubset(inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA)), populated_upgrade_snapshot
    event_foreign_keys = inspector.get_foreign_keys("allocation_events", schema=MIGRATION_TEST_SCHEMA)
    assert any(
        foreign_key["constrained_columns"] == ["proposal_version_id", "proposal_id"]
        and foreign_key["referred_table"] == "allocation_proposal_versions"
        and foreign_key["referred_columns"] == ["id", "proposal_id"]
        for foreign_key in event_foreign_keys
    )
    with engine.connect() as connection:
        batch = connection.execute(text("SELECT quality_grade, allocation_quality_grade FROM batches WHERE id = :id"), {"id": batch_id}).one()
        order = connection.execute(text("SELECT required_quality_grade, allocation_required_quality_grade FROM orders WHERE id = :id"), {"id": order_id}).one()
        assert batch == ("Grade A", None)
        assert order == ("Export Quality", None)

    downgrade_snapshot = _alembic(engine, "downgrade", PRE_ALLOCATION_REVISION)
    inspector = inspect(engine)
    assert "allocation_proposals" not in inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA), downgrade_snapshot
    assert "allocation_quality_grade" not in {column["name"] for column in inspector.get_columns("batches", schema=MIGRATION_TEST_SCHEMA)}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT quality_grade FROM batches WHERE id = :id"), {"id": batch_id}).scalar_one() == "Grade A"
        assert connection.execute(text("SELECT required_quality_grade FROM orders WHERE id = :id"), {"id": order_id}).scalar_one() == "Export Quality"

    _alembic(engine, "upgrade", "head")
    engine.dispose()
    admin_engine.dispose()
