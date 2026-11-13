import os
import subprocess
import sys
import threading
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.db_safety import create_guarded_admin_engine, load_test_target, verify_admin_connection


BACKEND_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
PRE_ALLOCATION_REVISION = "3585205556e4"
PRE_RESERVATION_REVISION = "a61f9b3d7e82"
MIGRATION_TEST_SCHEMA = "fungisync_migration_test"


def _test_database_url() -> str:
    load_test_target()
    return os.environ["TEST_ADMIN_DATABASE_URL"]


def _drop_migration_test_schema() -> None:
    """Remove all migration-test objects, including PostgreSQL native enums."""
    engine = create_guarded_admin_engine()
    try:
        with engine.begin() as connection:
            verify_admin_connection(connection, load_test_target())
            connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_migration_schema() -> None:
    """Keep migration DDL, especially schema-local enum types, out of later tests."""
    _drop_migration_test_schema()
    try:
        yield
    finally:
        _drop_migration_test_schema()


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["MIGRATION_DATABASE_URL"] = _test_database_url()
    environment.pop("DATABASE_URL", None)
    environment.pop("TEST_DATABASE_URL", None)
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
from sqlalchemy import text
from tests.db_safety import create_guarded_admin_engine
schema = __import__('os').environ['FUNGISYNC_ALEMBIC_SCHEMA']
with create_guarded_admin_engine(connect_args={'options': f'-csearch_path={schema}'}).connect() as connection:
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
    admin_engine = create_guarded_admin_engine()
    engine = create_guarded_admin_engine(connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})
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


def test_reservation_migration_preserves_allocation_records_and_reverses_cleanly() -> None:
    """Exercise the reservation migration from the actual Phase 3.2.2 schema."""
    test_url = _test_database_url()
    admin_engine = create_guarded_admin_engine()
    engine = create_guarded_admin_engine(connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})
    grower_id, buyer_id, batch_id, order_id, alternate_batch_id, alternate_order_id, proposal_id, version_id, line_id, event_id, reservation_id, second_proposal_id, second_version_id, second_line_id, mismatch_reservation_id, update_first_line_id, reservation_first_line_id, update_first_reservation_id, reservation_first_reservation_id = (uuid4() for _ in range(19))

    with admin_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {MIGRATION_TEST_SCHEMA}"))
    _alembic(engine, "stamp", "base")
    _alembic(engine, "upgrade", PRE_RESERVATION_REVISION)

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO growers (id, name, phone, location) VALUES (:id, 'Reservation grower', '9999999999', 'Pune')"), {"id": grower_id})
        connection.execute(text("INSERT INTO buyers (id, name, location, contact) VALUES (:id, 'Reservation buyer', 'Pune', 'buyer@example.invalid')"), {"id": buyer_id})
        connection.execute(text("""INSERT INTO batches
            (id, grower_id, species, expected_kg, confirmed_kg, harvest_date, status)
            VALUES (:id, :grower_id, 'oyster', 5.000, 5.000, DATE '2026-11-10', 'confirmed')"""), {"id": batch_id, "grower_id": grower_id})
        connection.execute(text("""INSERT INTO orders
            (id, buyer_id, species, quantity_kg, price_limit, delivery_date, status)
            VALUES (:id, :buyer_id, 'oyster', 5.000, 100.00, DATE '2026-11-11', 'pending')"""), {"id": order_id, "buyer_id": buyer_id})
        connection.execute(text("""INSERT INTO batches
            (id, grower_id, species, expected_kg, confirmed_kg, harvest_date, status)
            VALUES (:id, :grower_id, 'oyster', 5.000, 5.000, DATE '2026-11-10', 'confirmed')"""), {"id": alternate_batch_id, "grower_id": grower_id})
        connection.execute(text("""INSERT INTO orders
            (id, buyer_id, species, quantity_kg, price_limit, delivery_date, status)
            VALUES (:id, :buyer_id, 'oyster', 5.000, 100.00, DATE '2026-11-11', 'pending')"""), {"id": alternate_order_id, "buyer_id": buyer_id})
        connection.execute(text("INSERT INTO allocation_proposals (id, status, ruleset_version) VALUES (:id, 'DRAFT', 'phase-3.2.2')"), {"id": proposal_id})
        connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :proposal_id, 1)"), {"id": version_id, "proposal_id": proposal_id})
        connection.execute(text("""INSERT INTO allocation_lines (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version_id, :batch_id, :order_id, 1.000)"""), {"id": line_id, "version_id": version_id, "batch_id": batch_id, "order_id": order_id})
        connection.execute(text("""INSERT INTO allocation_events (id, proposal_id, proposal_version_id, event_type, details)
            VALUES (:id, :proposal_id, :version_id, 'proposal_generated', CAST('{\"advisory\": true}' AS json))"""), {"id": event_id, "proposal_id": proposal_id, "version_id": version_id})
        connection.execute(text("INSERT INTO allocation_proposals (id, status) VALUES (:id, 'DRAFT')"), {"id": second_proposal_id})
        connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :proposal_id, 1)"), {"id": second_version_id, "proposal_id": second_proposal_id})
        connection.execute(text("""INSERT INTO allocation_lines (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version_id, :batch_id, :order_id, 2.000)"""), {"id": second_line_id, "version_id": second_version_id, "batch_id": batch_id, "order_id": order_id})
        for race_line_id in (update_first_line_id, reservation_first_line_id):
            connection.execute(text("""INSERT INTO allocation_lines (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
                VALUES (:id, :version_id, :batch_id, :order_id, 3.000)"""), {"id": race_line_id, "version_id": second_version_id, "batch_id": batch_id, "order_id": order_id})

    upgraded_snapshot = _alembic(engine, "upgrade", "head")
    inspector = inspect(engine)
    assert {"allocation_reservations", "allocation_operation_idempotency"}.issubset(inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA)), upgraded_snapshot
    proposal_columns = {column["name"]: column for column in inspector.get_columns("allocation_proposals", schema=MIGRATION_TEST_SCHEMA)}
    assert {"submitted_at", "approved_at", "rejected_at", "cancelled_at", "expires_at", "terminal_reason_code", "terminal_reason"}.issubset(proposal_columns)
    assert all(proposal_columns[column]["nullable"] for column in {"submitted_at", "approved_at", "rejected_at", "cancelled_at", "expires_at", "terminal_reason_code", "terminal_reason"})
    reservation_foreign_keys = inspector.get_foreign_keys("allocation_reservations", schema=MIGRATION_TEST_SCHEMA)
    assert any(foreign_key["constrained_columns"] == ["proposal_version_id", "proposal_id"] and foreign_key["referred_table"] == "allocation_proposal_versions" for foreign_key in reservation_foreign_keys)
    assert any(foreign_key["constrained_columns"] == ["allocation_line_id", "proposal_version_id"] and foreign_key["referred_table"] == "allocation_lines" for foreign_key in reservation_foreign_keys)
    event_foreign_keys = inspector.get_foreign_keys("allocation_events", schema=MIGRATION_TEST_SCHEMA)
    assert any(foreign_key["constrained_columns"] == ["reservation_id"] and foreign_key["referred_table"] == "allocation_reservations" for foreign_key in event_foreign_keys)
    idempotency_unique_constraints = inspector.get_unique_constraints("allocation_operation_idempotency", schema=MIGRATION_TEST_SCHEMA)
    assert any(constraint["column_names"] == ["operation_type", "idempotency_key"] for constraint in idempotency_unique_constraints)
    with engine.connect() as connection:
        preserved = connection.execute(text("""SELECT status, ruleset_version, submitted_at, approved_at, rejected_at,
            cancelled_at, expires_at, terminal_reason_code, terminal_reason
            FROM allocation_proposals WHERE id = :id"""), {"id": proposal_id}).one()
        assert preserved == ("DRAFT", "phase-3.2.2", None, None, None, None, None, None, None)
        assert connection.execute(text("SELECT proposed_quantity_kg FROM allocation_lines WHERE id = :id"), {"id": line_id}).scalar_one() == 1
        assert connection.execute(text("SELECT reservation_id FROM allocation_events WHERE id = :id"), {"id": event_id}).scalar_one() is None

    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO allocation_reservations
            (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, expires_at)
            VALUES (:id, :line_id, :proposal_id, :version_id, 1.000, 'PENDING', now())"""), {
            "id": reservation_id, "line_id": line_id, "proposal_id": proposal_id, "version_id": version_id,
        })
        connection.execute(text("""UPDATE allocation_reservations
            SET status = 'COMMITTED', committed_at = now() WHERE id = :id"""), {"id": reservation_id})
        connection.execute(text("""UPDATE allocation_reservations
            SET status = 'RELEASED', terminal_at = now() WHERE id = :id"""), {"id": reservation_id})
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("""UPDATE allocation_lines
            SET batch_id = :batch_id, order_id = :order_id WHERE id = :id"""), {
            "id": second_line_id, "batch_id": alternate_batch_id, "order_id": alternate_order_id,
        })
        transaction.commit()
        assert connection.execute(text("""SELECT batch_id, order_id FROM allocation_lines
            WHERE id = :id"""), {"id": second_line_id}).one() == (alternate_batch_id, alternate_order_id)
        connection.commit()

        for statement in (
            "UPDATE allocation_reservations SET allocation_line_id = :other_id WHERE id = :id",
            "UPDATE allocation_reservations SET proposal_id = :other_id WHERE id = :id",
            "UPDATE allocation_reservations SET proposal_version_id = :other_id WHERE id = :id",
            "UPDATE allocation_reservations SET reserved_quantity_kg = 2.000 WHERE id = :id",
        ):
            transaction = connection.begin()
            with pytest.raises(IntegrityError, match="allocation reservation snapshot fields are immutable"):
                connection.execute(text(statement), {"id": reservation_id, "other_id": second_line_id if "allocation_line_id" in statement else second_proposal_id if "proposal_id" in statement else second_version_id})
            transaction.rollback()
        for statement in (
            "UPDATE allocation_lines SET id = :other_id WHERE id = :id",
            "UPDATE allocation_lines SET proposal_version_id = :other_id WHERE id = :id",
            "UPDATE allocation_lines SET batch_id = :other_id WHERE id = :id",
            "UPDATE allocation_lines SET order_id = :other_id WHERE id = :id",
            "UPDATE allocation_lines SET proposed_quantity_kg = 2.000 WHERE id = :id",
        ):
            transaction = connection.begin()
            with pytest.raises(IntegrityError, match="reserved allocation line snapshot fields are immutable"):
                connection.execute(text(statement), {"id": line_id, "other_id": alternate_batch_id if "batch_id" in statement else alternate_order_id if "order_id" in statement else second_line_id if "SET id" in statement else second_version_id})
            transaction.rollback()
        assert connection.execute(text("""SELECT batch_id, order_id FROM allocation_lines
            WHERE id = :id"""), {"id": line_id}).one() == (batch_id, order_id)
        connection.commit()
        transaction = connection.begin()
        with pytest.raises(IntegrityError, match="reservation quantity must equal its allocation line proposed quantity"):
            connection.execute(text("""INSERT INTO allocation_reservations
                (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, expires_at)
                VALUES (:id, :line_id, :proposal_id, :version_id, 1.000, 'PENDING', now())"""), {
                "id": mismatch_reservation_id, "line_id": second_line_id,
                "proposal_id": second_proposal_id, "version_id": second_version_id,
            })
        transaction.rollback()
        transaction = connection.begin()
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE allocation_reservations SET status = 'PENDING' WHERE id = :id"), {"id": reservation_id})
        transaction.rollback()

    race_engine = create_guarded_admin_engine(connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})

    def _run_in_thread(statement: str, values: dict[str, object], errors: list[BaseException], ready: threading.Event, start: threading.Event, done: threading.Event, worker: dict[str, int]) -> None:
        try:
            with race_engine.connect() as race_connection:
                transaction = race_connection.begin()
                worker["pid"] = race_connection.scalar(text("SELECT pg_backend_pid()"))
                ready.set()
                if not start.wait(timeout=5):
                    raise RuntimeError("worker was not released to execute its competing statement")
                race_connection.execute(text(statement), values)
                transaction.commit()
        except BaseException as exc:  # Assert the database error after synchronization.
            errors.append(exc)
        finally:
            done.set()

    def _assert_worker_blocked(controller, worker_pid: int, blocker_pid: int, done: threading.Event, scenario: str) -> None:
        deadline = monotonic() + 5
        diagnostics: list[object] = []
        while monotonic() < deadline:
            activity = controller.execute(text("""
                SELECT pg_blocking_pids(:worker_pid), wait_event_type, wait_event, state, query
                FROM pg_stat_activity WHERE pid = :worker_pid
            """), {"worker_pid": worker_pid}).one_or_none()
            diagnostics.append(activity)
            if activity is not None and blocker_pid in activity[0] and activity[1] == "Lock":
                return
            if done.is_set():
                raise AssertionError(f"{scenario} worker completed before blocking; activity={diagnostics!r}")
            done.wait(0.05)
        raise AssertionError(f"{scenario} worker did not block on backend {blocker_pid}; activity={diagnostics!r}")

    with engine.connect() as update_connection:
        update_transaction = update_connection.begin()
        update_connection.execute(text("""UPDATE allocation_lines
            SET batch_id = :batch_id, order_id = :order_id WHERE id = :id"""), {
            "id": update_first_line_id, "batch_id": alternate_batch_id, "order_id": alternate_order_id,
        })
        errors: list[BaseException] = []
        ready = threading.Event()
        start = threading.Event()
        done = threading.Event()
        worker: dict[str, int] = {}
        thread = threading.Thread(target=_run_in_thread, args=(
            """INSERT INTO allocation_reservations
               (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, expires_at)
               VALUES (:id, :line_id, :proposal_id, :version_id, 3.000, 'PENDING', now())""",
            {"id": update_first_reservation_id, "line_id": update_first_line_id, "proposal_id": second_proposal_id, "version_id": second_version_id}, errors, ready, start, done, worker,
        ))
        thread.start()
        try:
            assert ready.wait(timeout=5)
            start.set()
            _assert_worker_blocked(update_connection, worker["pid"], update_connection.scalar(text("SELECT pg_backend_pid()")), done, "update-first")
            update_transaction.commit()
        finally:
            start.set()
            if update_transaction.is_active:
                update_transaction.rollback()
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert done.is_set() and not errors

    with engine.connect() as reservation_connection:
        reservation_transaction = reservation_connection.begin()
        reservation_connection.execute(text("""INSERT INTO allocation_reservations
            (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, expires_at)
            VALUES (:id, :line_id, :proposal_id, :version_id, 3.000, 'PENDING', now())"""), {
            "id": reservation_first_reservation_id, "line_id": reservation_first_line_id,
            "proposal_id": second_proposal_id, "version_id": second_version_id,
        })
        errors = []
        ready = threading.Event()
        start = threading.Event()
        done = threading.Event()
        worker = {}
        thread = threading.Thread(target=_run_in_thread, args=(
            """UPDATE allocation_lines SET batch_id = :batch_id, order_id = :order_id
               WHERE id = :id""",
            {"id": reservation_first_line_id, "batch_id": alternate_batch_id,
             "order_id": alternate_order_id}, errors, ready, start, done, worker,
        ))
        thread.start()
        try:
            assert ready.wait(timeout=5)
            start.set()
            _assert_worker_blocked(reservation_connection, worker["pid"], reservation_connection.scalar(text("SELECT pg_backend_pid()")), done, "reservation-first")
            reservation_transaction.commit()
        finally:
            start.set()
            if reservation_transaction.is_active:
                reservation_transaction.rollback()
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert done.is_set() and len(errors) == 1 and isinstance(errors[0], IntegrityError)
        assert "reserved allocation line snapshot fields are immutable" in str(errors[0])
    race_engine.dispose()

    downgraded_snapshot = _alembic(engine, "downgrade", PRE_RESERVATION_REVISION)
    inspector = inspect(engine)
    assert "allocation_reservations" not in inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA), downgraded_snapshot
    assert "allocation_operation_idempotency" not in inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA), downgraded_snapshot
    assert "reservation_id" not in {column["name"] for column in inspector.get_columns("allocation_events", schema=MIGRATION_TEST_SCHEMA)}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT proposed_quantity_kg FROM allocation_lines WHERE id = :id"), {"id": line_id}).scalar_one() == 1
        assert connection.execute(text("SELECT event_type FROM allocation_events WHERE id = :id"), {"id": event_id}).scalar_one() == "proposal_generated"

    _alembic(engine, "upgrade", "head")
    inspector = inspect(engine)
    assert {"allocation_reservations", "allocation_operation_idempotency"}.issubset(inspector.get_table_names(schema=MIGRATION_TEST_SCHEMA))
    assert "terminal_at" in {column["name"] for column in inspector.get_columns("allocation_reservations", schema=MIGRATION_TEST_SCHEMA)}
    with engine.connect() as connection:
        triggers = set(connection.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid IN ('allocation_reservations'::regclass, 'allocation_lines'::regclass) AND NOT tgisinternal")).scalars())
    assert {"trg_allocation_reservations_snapshot", "trg_allocation_lines_reservation_snapshot"}.issubset(triggers)
    assert {"ix_allocation_reservations_status_expires_at", "ix_allocation_operation_idempotency_proposal_id"}.issubset({index["name"] for index in inspector.get_indexes("allocation_reservations", schema=MIGRATION_TEST_SCHEMA)} | {index["name"] for index in inspector.get_indexes("allocation_operation_idempotency", schema=MIGRATION_TEST_SCHEMA)})
    engine.dispose()
    admin_engine.dispose()
