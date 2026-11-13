"""PostgreSQL migration tests for single, immutable proposal versions."""

from __future__ import annotations

import threading
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.test_allocation_migrations import (
    MIGRATION_TEST_SCHEMA,
    _alembic,
    _test_database_url,
)
from tests.db_safety import create_guarded_admin_engine, load_test_target, verify_admin_connection


@pytest.fixture
def integrity_engine():
    url = _test_database_url()
    admin = create_guarded_admin_engine()
    with admin.begin() as connection:
        verify_admin_connection(connection, load_test_target())
        connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {MIGRATION_TEST_SCHEMA}"))
    engine = create_guarded_admin_engine(connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})
    try:
        _alembic(engine, "stamp", "base")
        _alembic(engine, "upgrade", "c3d3f1a9b742")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            verify_admin_connection(connection, load_test_target())
            connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        admin.dispose()


def _proposal(connection, status: str = "DRAFT"):
    proposal_id = uuid4()
    connection.execute(
        text("INSERT INTO allocation_proposals (id, status) VALUES (:id, :status)"),
        {"id": proposal_id, "status": status},
    )
    return proposal_id


def _assert_integrity_error(connection, statement: str, values: dict[str, object], message: str) -> None:
    transaction = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError, match=message):
            connection.execute(text(statement), values)
    finally:
        transaction.rollback()


def _blocking_snapshot(connection, pid: int):
    return connection.execute(text("""
        WITH RECURSIVE blockers(blocker_pid, depth, path) AS (
            SELECT direct.pid, 1, ARRAY[:pid, direct.pid]::integer[]
            FROM unnest(pg_blocking_pids(:pid)) AS direct(pid)
            UNION ALL
            SELECT next.pid, blockers.depth + 1, blockers.path || next.pid
            FROM blockers
            CROSS JOIN LATERAL unnest(pg_blocking_pids(blockers.blocker_pid)) AS next(pid)
            WHERE NOT next.pid = ANY(blockers.path)
        )
        SELECT activity.pid, activity.query, activity.state,
               activity.wait_event_type, activity.wait_event,
               pg_blocking_pids(activity.pid) AS direct_blockers,
               COALESCE(
                   (SELECT array_agg(DISTINCT blocker_pid ORDER BY blocker_pid) FROM blockers),
                   ARRAY[]::integer[]
               ) AS blocker_chain
        FROM pg_stat_activity AS activity
        WHERE activity.pid = :pid
    """), {"pid": pid}).mappings().one_or_none()


def _is_waiting_on_proposal_insert(snapshot, controller_pid: int) -> bool:
    if snapshot is None:
        return False
    query = snapshot["query"] or ""
    return (
        query.lstrip().upper().startswith("INSERT INTO ALLOCATION_PROPOSAL_VERSIONS")
        and snapshot["state"] == "active"
        and snapshot["wait_event_type"] == "Lock"
        and controller_pid in (snapshot["blocker_chain"] or ())
    )


def test_version_triggers_enforce_immutable_single_version_and_preserve_rows(integrity_engine) -> None:
    proposal_id, version_id = uuid4(), uuid4()
    second_proposal_id, non_draft_id, history_proposal_id = uuid4(), uuid4(), uuid4()
    history_version_id, line_id, main_line_id, reservation_id = uuid4(), uuid4(), uuid4(), uuid4()

    # Start with the reservation foundation, seed representative existing data,
    # then upgrade the new migration over it.
    grower_id, buyer_id, batch_id, order_id = (uuid4() for _ in range(4))
    with integrity_engine.begin() as connection:
        connection.execute(text("INSERT INTO growers (id, name, phone, location) VALUES (:id, 'Version grower', '9999999999', 'Pune')"), {"id": grower_id})
        connection.execute(text("INSERT INTO buyers (id, name, location, contact) VALUES (:id, 'Version buyer', 'Pune', 'version@example.invalid')"), {"id": buyer_id})
        connection.execute(text("""INSERT INTO batches
            (id, grower_id, species, expected_kg, confirmed_kg, harvest_date, status)
            VALUES (:id, :grower_id, 'oyster', 5.000, 5.000, DATE '2026-11-10', 'confirmed')"""), {"id": batch_id, "grower_id": grower_id})
        connection.execute(text("""INSERT INTO orders
            (id, buyer_id, species, quantity_kg, price_limit, delivery_date, status)
            VALUES (:id, :buyer_id, 'oyster', 5.000, 100.00, DATE '2026-11-11', 'pending')"""), {"id": order_id, "buyer_id": buyer_id})
        for pid in (proposal_id, history_proposal_id):
            connection.execute(text("INSERT INTO allocation_proposals (id, status) VALUES (:id, 'DRAFT')"), {"id": pid})
        connection.execute(text("INSERT INTO allocation_proposals (id, status) VALUES (:id, 'APPROVED')"), {"id": non_draft_id})
        connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)"), {"id": version_id, "pid": proposal_id})
        connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)"), {"id": history_version_id, "pid": history_proposal_id})
        connection.execute(text("""INSERT INTO allocation_lines (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version_id, :batch_id, :order_id, 1.000)"""), {"id": main_line_id, "version_id": version_id, "batch_id": batch_id, "order_id": order_id})
        connection.execute(text("""INSERT INTO allocation_lines (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version_id, :batch_id, :order_id, 1.000)"""), {"id": line_id, "version_id": history_version_id, "batch_id": batch_id, "order_id": order_id})
        connection.execute(text("""INSERT INTO allocation_reservations
            (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, committed_at, terminal_at)
            VALUES (:id, :line_id, :proposal_id, :version_id, 1.000, 'RELEASED', now(), now())"""), {"id": reservation_id, "line_id": line_id, "proposal_id": history_proposal_id, "version_id": history_version_id})
    _alembic(integrity_engine, "upgrade", "head")

    trigger_names = {
        "trg_allocation_proposal_versions_immutable",
        "trg_allocation_proposal_versions_initial_only",
    }
    with integrity_engine.connect() as connection:
        outer_transaction = connection.begin()
        found = set(connection.execute(text("""
            SELECT tgname FROM pg_trigger
            WHERE tgrelid = 'allocation_proposal_versions'::regclass AND NOT tgisinternal
        """)).scalars())
        assert trigger_names.issubset(found)
        original_version = connection.execute(text("""
            SELECT id, proposal_id, version_number, created_at, updated_at
            FROM allocation_proposal_versions WHERE id = :id
        """), {"id": version_id}).one()

        _assert_integrity_error(connection,
            "UPDATE allocation_proposal_versions SET version_number = 2 WHERE id = :id",
            {"id": version_id}, "allocation proposal versions are immutable")
        _assert_integrity_error(connection,
            "UPDATE allocation_proposal_versions SET updated_at = now() WHERE id = :id",
            {"id": version_id}, "allocation proposal versions are immutable")
        _assert_integrity_error(connection,
            "UPDATE allocation_proposal_versions SET proposal_id = :pid WHERE id = :id",
            {"id": version_id, "pid": second_proposal_id}, "allocation proposal versions are immutable")
        _assert_integrity_error(connection,
            "DELETE FROM allocation_proposal_versions WHERE id = :id",
            {"id": version_id}, "allocation proposal versions are immutable")
        assert connection.execute(text("""
            SELECT id, proposal_id, version_number, created_at, updated_at
            FROM allocation_proposal_versions WHERE id = :id
        """), {"id": version_id}).one() == original_version
        assert connection.execute(text("SELECT proposal_version_id FROM allocation_lines WHERE id = :id"), {"id": main_line_id}).scalar_one() == version_id

        # New DRAFT proposal + version 1 remains valid. Version 2 and a repeated
        # version 1 are both rejected by the single-version trigger.
        new_proposal_id = _proposal(connection)
        new_version_id = uuid4()
        connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)"), {"id": new_version_id, "pid": new_proposal_id})
        _assert_integrity_error(connection,
            "INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 2)",
            {"id": uuid4(), "pid": new_proposal_id}, "single-version policy requires")
        _assert_integrity_error(connection,
            "INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)",
            {"id": uuid4(), "pid": new_proposal_id}, "already has its single permitted version")
        _assert_integrity_error(connection,
            "INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)",
            {"id": uuid4(), "pid": non_draft_id}, "only be created for DRAFT")
        _assert_integrity_error(connection,
            "INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)",
            {"id": uuid4(), "pid": history_proposal_id}, "reservation history exists")
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 0)"), {"id": uuid4(), "pid": _proposal(connection)})
        outer_transaction.rollback()

    # Downgrade removes only this migration's enforcement, and re-upgrade
    # reinstalls it without dropping proposal/version/line/reservation records.
    _alembic(integrity_engine, "downgrade", "c3d3f1a9b742")
    with integrity_engine.connect() as connection:
        remaining = set(connection.execute(text("""
            SELECT tgname FROM pg_trigger
            WHERE tgrelid = 'allocation_proposal_versions'::regclass AND NOT tgisinternal
        """)).scalars())
        assert not (remaining & trigger_names)
        functions = set(connection.execute(text("""
            SELECT proname FROM pg_proc
            WHERE proname IN (
                'enforce_allocation_proposal_version_immutable',
                'enforce_initial_allocation_proposal_version'
            )
        """)).scalars())
        assert not functions
        assert connection.execute(text("SELECT count(*) FROM allocation_proposal_versions WHERE id IN (:id, :history_id)"), {"id": version_id, "history_id": history_version_id}).scalar_one() == 2
        assert connection.execute(text("SELECT count(*) FROM allocation_lines WHERE id = :id AND proposal_version_id = :version_id"), {"id": line_id, "version_id": history_version_id}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM allocation_reservations WHERE id = :id"), {"id": reservation_id}).scalar_one() == 1
    _alembic(integrity_engine, "upgrade", "head")
    with integrity_engine.connect() as connection:
        restored = set(connection.execute(text("""
            SELECT tgname FROM pg_trigger
            WHERE tgrelid = 'allocation_proposal_versions'::regclass AND NOT tgisinternal
        """)).scalars())
        assert trigger_names.issubset(restored)


def test_concurrent_initial_version_inserts_serialize_on_proposal_row(integrity_engine) -> None:
    _alembic(integrity_engine, "upgrade", "head")
    proposal_id = uuid4()
    with integrity_engine.begin() as connection:
        connection.execute(text("INSERT INTO allocation_proposals (id, status) VALUES (:id, 'DRAFT')"), {"id": proposal_id})

    controller = integrity_engine.connect()
    controller_transaction = controller.begin()
    controller.execute(text("SELECT id FROM allocation_proposals WHERE id = :id FOR UPDATE"), {"id": proposal_id})
    controller_pid = controller.scalar(text("SELECT pg_backend_pid()"))

    ready = [threading.Event(), threading.Event()]
    start = threading.Event()
    done = [threading.Event(), threading.Event()]
    errors: list[BaseException] = []
    worker_pids: list[int | None] = [None, None]
    successes: list[int] = []

    def insert_initial_version(index: int) -> None:
        try:
            with integrity_engine.begin() as connection:
                worker_pids[index] = connection.scalar(text("SELECT pg_backend_pid()"))
                ready[index].set()
                if not start.wait(timeout=10):
                    raise RuntimeError("concurrent insert worker was not released")
                connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)"), {"id": uuid4(), "pid": proposal_id})
            successes.append(index)
        except BaseException as error:
            errors.append(error)
        finally:
            done[index].set()

    threads = [threading.Thread(target=insert_initial_version, args=(index,)) for index in range(2)]
    started_threads: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
        assert all(event.wait(timeout=10) for event in ready)
        start.set()
        deadline = monotonic() + 10
        blocked: set[int] = set()
        snapshots = [None, None]
        completed_unblocked = False
        while monotonic() < deadline and len(blocked) < 2:
            for index, pid in enumerate(worker_pids):
                if pid is None or index in blocked:
                    continue
                snapshots[index] = _blocking_snapshot(controller, pid)
                if _is_waiting_on_proposal_insert(snapshots[index], controller_pid):
                    blocked.add(index)
                elif done[index].is_set():
                    completed_unblocked = True
                    break
            if completed_unblocked:
                break
        if len(blocked) != 2:
            details = []
            for index, pid in enumerate(worker_pids):
                snapshot = snapshots[index]
                if snapshot is None and pid is not None:
                    snapshot = _blocking_snapshot(controller, pid)
                if snapshot is None:
                    details.append(f"worker {index}: pid={pid}, state=unavailable, done={done[index].is_set()}")
                else:
                    details.append(
                        f"worker {index}: pid={pid}, query={snapshot['query']!r}, "
                        f"state={snapshot['state']!r}, wait_event_type={snapshot['wait_event_type']!r}, "
                        f"wait_event={snapshot['wait_event']!r}, direct_blockers={snapshot['direct_blockers']!r}, "
                        f"blocker_chain={snapshot['blocker_chain']!r}, done={done[index].is_set()}"
                    )
            raise AssertionError(
                "both workers did not reach the proposal-row lock while executing the intended INSERT; "
                + "; ".join(details)
            )
        controller_transaction.commit()
    finally:
        if controller_transaction.is_active:
            controller_transaction.rollback()
        controller.close()
        start.set()
        for thread in started_threads:
            thread.join()
    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == 1
    assert len(errors) == 1 and isinstance(errors[0], IntegrityError)
    with integrity_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM allocation_proposal_versions WHERE proposal_id = :id"), {"id": proposal_id}).scalar_one() == 1
