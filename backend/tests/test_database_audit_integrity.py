"""PostgreSQL migration and lifecycle checks for database audit integrity."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.allocation_persistence import persist_advisory_draft
from app.models import (
    AllocationEvent, AllocationLine, AllocationProposal, AllocationProposalStatus,
    AllocationReservation, AllocationReservationStatus,
)
from app.reservation_service import (
    InvalidAllocationLineError, InvalidTerminalReasonError, ProposalTargetsChangedError,
    _terminal_reason,
    approve_allocation_proposal, cancel_allocation_proposal,
    expire_pending_reservations, reject_allocation_proposal,
    release_committed_reservations, submit_allocation_proposal,
)
from tests.test_allocation_migrations import MIGRATION_TEST_SCHEMA, _alembic, _test_database_url
from tests.db_safety import create_guarded_admin_engine, load_test_target, verify_admin_connection
from tests.test_allocation_persistence import _draft, _seed as _draft_seed
from tests.test_proposal_submission import _factory, _proposal


PARENT_REVISION = "d7e4b8c2a913"
REVISION = "f8a3c9d2e641"


@pytest.fixture()
def audit_engine():
    url = _test_database_url()
    admin = create_guarded_admin_engine()
    with admin.begin() as connection:
        verify_admin_connection(connection, load_test_target())
        connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {MIGRATION_TEST_SCHEMA}"))
    engine = create_guarded_admin_engine(connect_args={"options": f"-csearch_path={MIGRATION_TEST_SCHEMA}"})
    try:
        _alembic(engine, "stamp", "base")
        _alembic(engine, "upgrade", PARENT_REVISION)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            verify_admin_connection(connection, load_test_target())
            connection.execute(text(f"DROP SCHEMA IF EXISTS {MIGRATION_TEST_SCHEMA} CASCADE"))
        admin.dispose()


def _seed_sql(connection, *, with_line=True, with_event=False):
    grower_id, buyer_id, batch_id, order_id, proposal_id, version_id, line_id, event_id = (uuid4() for _ in range(8))
    connection.execute(text("INSERT INTO growers (id, name, phone, location) VALUES (:id, 'Audit grower', '9999999999', 'Pune')"), {"id": grower_id})
    connection.execute(text("INSERT INTO buyers (id, name, location, contact) VALUES (:id, 'Audit buyer', 'Pune', 'audit@example.invalid')"), {"id": buyer_id})
    connection.execute(text("""INSERT INTO batches
        (id, grower_id, species, expected_kg, confirmed_kg, harvest_date, asking_price_per_kg, allocation_quality_grade, status)
        VALUES (:id, :grower_id, 'oyster', 10.000, 10.000, DATE '2026-11-10', 100.00, 'PREMIUM', 'confirmed')"""),
        {"id": batch_id, "grower_id": grower_id})
    connection.execute(text("""INSERT INTO orders
        (id, buyer_id, species, quantity_kg, price_limit, delivery_date, allocation_required_quality_grade, status)
        VALUES (:id, :buyer_id, 'oyster', 10.000, 100.00, DATE '2026-11-11', 'PREMIUM', 'pending')"""),
        {"id": order_id, "buyer_id": buyer_id})
    connection.execute(text("INSERT INTO allocation_proposals (id, status) VALUES (:id, 'DRAFT')"), {"id": proposal_id})
    connection.execute(text("INSERT INTO allocation_proposal_versions (id, proposal_id, version_number) VALUES (:id, :pid, 1)"),
                       {"id": version_id, "pid": proposal_id})
    if with_line:
        connection.execute(text("""INSERT INTO allocation_lines
            (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version, :batch, :order, 1.000)"""),
            {"id": line_id, "version": version_id, "batch": batch_id, "order": order_id})
    if with_event:
        connection.execute(text("""INSERT INTO allocation_events
            (id, proposal_id, proposal_version_id, event_type, details)
            VALUES (:id, :proposal, :version, 'proposal_generated', CAST('{"advisory": true}' AS json))"""),
            {"id": event_id, "proposal": proposal_id, "version": version_id})
    return batch_id, order_id, proposal_id, version_id, line_id, event_id


def _reject(connection, statement, params, message):
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError, match=message) as raised:
            connection.execute(text(statement), params)
        assert raised.value.orig.sqlstate == "23514"
    finally:
        savepoint.rollback()


def test_existing_event_survives_upgrade_and_events_are_append_only(audit_engine):
    with audit_engine.begin() as connection:
        _, _, proposal_id, version_id, _, existing_id = _seed_sql(connection, with_event=True)
    _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.begin() as connection:
        original = connection.execute(text("SELECT id, event_type, details::text FROM allocation_events WHERE id=:id"),
                                      {"id": existing_id}).one()
        inserted_id = uuid4()
        connection.execute(text("""INSERT INTO allocation_events
            (id, proposal_id, proposal_version_id, event_type)
            VALUES (:id, :proposal, :version, 'audit_compensation')"""),
            {"id": inserted_id, "proposal": proposal_id, "version": version_id})
        _reject(connection, "UPDATE allocation_events SET event_type='changed' WHERE id=:id",
                {"id": existing_id}, "allocation events are append-only")
        _reject(connection, "UPDATE allocation_events SET event_type=event_type WHERE id=:id",
                {"id": existing_id}, "allocation events are append-only")
        _reject(connection, "DELETE FROM allocation_events WHERE id=:id",
                {"id": existing_id}, "allocation events are append-only")
        _reject(connection, "TRUNCATE allocation_events, allocation_operation_idempotency",
                {}, "TRUNCATE is prohibited")
        assert connection.execute(text("SELECT id, event_type, details::text FROM allocation_events WHERE id=:id"),
                                  {"id": existing_id}).one() == original
        assert connection.scalar(text("SELECT count(*) FROM allocation_events")) == 2

    _alembic(audit_engine, "downgrade", PARENT_REVISION)
    with audit_engine.connect() as connection:
        names = set(connection.execute(text("""SELECT tgname FROM pg_trigger
            WHERE tgrelid='allocation_events'::regclass AND NOT tgisinternal""")).scalars())
        assert not names.intersection({"trg_allocation_events_append_only", "trg_allocation_events_no_truncate"})
        line_triggers = set(connection.execute(text("""SELECT tgname FROM pg_trigger
            WHERE tgrelid='allocation_lines'::regclass AND NOT tgisinternal""")).scalars())
        assert "trg_allocation_lines_draft_insert" not in line_triggers
        assert "trg_allocation_lines_reservation_snapshot" in line_triggers
        version_triggers = set(connection.execute(text("""SELECT tgname FROM pg_trigger
            WHERE tgrelid='allocation_proposal_versions'::regclass AND NOT tgisinternal""")).scalars())
        assert {"trg_allocation_proposal_versions_immutable", "trg_allocation_proposal_versions_initial_only"}.issubset(version_triggers)
        assert connection.scalar(text("SELECT count(*) FROM allocation_events")) == 2
    proposal_checks = {item["name"] for item in inspect(audit_engine).get_check_constraints(
        "allocation_proposals", schema=MIGRATION_TEST_SCHEMA,
    )}
    assert "ck_allocation_proposals_pending_timestamps" not in proposal_checks
    _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.begin() as connection:
        names = set(connection.execute(text("""SELECT tgname FROM pg_trigger
            WHERE tgrelid='allocation_events'::regclass AND NOT tgisinternal""")).scalars())
        assert {"trg_allocation_events_append_only", "trg_allocation_events_no_truncate"}.issubset(names)
        _reject(connection, "DELETE FROM allocation_events WHERE id=:id", {"id": inserted_id}, "append-only")
        assert connection.scalar(text("SELECT count(*) FROM allocation_events")) == 2


def test_invalid_historical_row_aborts_constraint_migration_without_rewrite(audit_engine):
    with audit_engine.begin() as connection:
        _, _, proposal_id, _, _, _ = _seed_sql(connection)
        connection.execute(text("UPDATE allocation_proposals SET status='PENDING_APPROVAL' WHERE id=:id"),
                           {"id": proposal_id})
    with pytest.raises(RuntimeError, match="ck_allocation_proposals_pending_timestamps"):
        _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PARENT_REVISION
        assert connection.scalar(text("SELECT status::text FROM allocation_proposals WHERE id=:id"),
                                 {"id": proposal_id}) == "PENDING_APPROVAL"
        assert connection.scalar(text("SELECT submitted_at FROM allocation_proposals WHERE id=:id"),
                                 {"id": proposal_id}) is None


def test_draft_line_insert_and_draft_persistence_work_under_migration(audit_engine):
    _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.begin() as connection:
        batch_id, order_id, proposal_id, version_id, _, _ = _seed_sql(connection, with_line=False)
        connection.execute(text("""INSERT INTO allocation_lines
            (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version, :batch, :order, 1.000)"""),
            {"id": uuid4(), "version": version_id, "batch": batch_id, "order": order_id})
    with Session(audit_engine) as session:
        assert session.get(AllocationProposal, proposal_id).status is AllocationProposalStatus.DRAFT

    factory = _factory(audit_engine)
    order_id, batch_ids = _draft_seed(factory)
    result = _draft(factory, order_id, batch_ids)
    persisted = persist_advisory_draft(factory, result, ruleset_version="audit-test")
    with Session(audit_engine) as session:
        assert session.get(AllocationProposal, persisted.proposal_id).status is AllocationProposalStatus.DRAFT
        assert len(persisted.line_ids) == 1
        assert session.get(AllocationEvent, persisted.event_id).event_type == "proposal_generated"


@pytest.mark.parametrize("terminal", [None, "approved", "rejected", "cancelled", "expired", "released"])
def test_direct_line_insert_is_rejected_after_submission(audit_engine, terminal):
    _alembic(audit_engine, "upgrade", REVISION)
    with Session(audit_engine) as session:
        proposal, version, batch, order, _ = _proposal(session)
        ids = (proposal.id, version.id, batch.id, order.id)
    factory = _factory(audit_engine)
    submit_allocation_proposal(factory, ids[0])
    if terminal == "approved":
        approve_allocation_proposal(factory, ids[0])
    elif terminal == "rejected":
        reject_allocation_proposal(factory, ids[0], reason_code="audit_reject")
    elif terminal == "cancelled":
        cancel_allocation_proposal(factory, ids[0], reason_code="audit_cancel")
    elif terminal == "expired":
        with audit_engine.begin() as connection:
            past = datetime(2000, 1, 1, tzinfo=timezone.utc)
            connection.execute(text("UPDATE allocation_proposals SET expires_at=:past WHERE id=:id"), {"past": past, "id": ids[0]})
            connection.execute(text("UPDATE allocation_reservations SET expires_at=:past WHERE proposal_id=:id"),
                               {"past": past, "id": ids[0]})
        expire_pending_reservations(factory, ids[0])
    elif terminal == "released":
        approve_allocation_proposal(factory, ids[0])
        release_committed_reservations(factory, ids[0], reason_code="audit_release")
    with audit_engine.begin() as connection:
        before = connection.scalar(text("SELECT count(*) FROM allocation_lines WHERE proposal_version_id=:id"), {"id": ids[1]})
        _reject(connection, """INSERT INTO allocation_lines
            (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
            VALUES (:id, :version, :batch, :order, 1.000)""",
            {"id": uuid4(), "version": ids[1], "batch": ids[2], "order": ids[3]},
            "allocation lines can only be inserted for DRAFT")
        assert connection.scalar(text("SELECT count(*) FROM allocation_lines WHERE proposal_version_id=:id"),
                                 {"id": ids[1]}) == before


def test_row_local_checks_and_valid_histories(audit_engine):
    _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.begin() as connection:
        batch_id, order_id, proposal_id, version_id, line_id, _ = _seed_sql(connection)
        _reject(connection, "UPDATE allocation_proposals SET status='PENDING_APPROVAL' WHERE id=:id",
                {"id": proposal_id}, "ck_allocation_proposals_pending_timestamps")
        _reject(connection, "UPDATE allocation_proposals SET status='REJECTED' WHERE id=:id",
                {"id": proposal_id}, "ck_allocation_proposals_rejected_metadata")
        _reject(connection, "UPDATE allocation_proposals SET status='CANCELLED' WHERE id=:id",
                {"id": proposal_id}, "ck_allocation_proposals_cancelled_metadata")
        _reject(connection, "UPDATE allocation_proposals SET status='RELEASED' WHERE id=:id",
                {"id": proposal_id}, "ck_allocation_proposals_released_reason")
        connection.execute(text("""INSERT INTO allocation_reservations
            (id, allocation_line_id, proposal_id, proposal_version_id, reserved_quantity_kg, status, expires_at)
            VALUES (:id, :line, :proposal, :version, 1.000, 'PENDING', now() - interval '1 day')"""),
            {"id": uuid4(), "line": line_id, "proposal": proposal_id, "version": version_id})
        _reject(connection, "UPDATE allocation_reservations SET status='REJECTED', terminal_at=now() WHERE allocation_line_id=:line",
                {"line": line_id}, "ck_allocation_reservations_rejected_cancelled_reason")
        _reject(connection, "UPDATE allocation_reservations SET status='CANCELLED', terminal_at=now(), terminal_reason_code='   ' WHERE allocation_line_id=:line",
                {"line": line_id}, "ck_allocation_reservations_rejected_cancelled_reason")
        assert connection.scalar(text("SELECT status::text FROM allocation_reservations WHERE allocation_line_id=:line"),
                                 {"line": line_id}) == "PENDING"
        assert connection.scalar(text("SELECT status::text FROM allocation_proposals WHERE id=:id"),
                                 {"id": proposal_id}) == "DRAFT"

    factory = _factory(audit_engine)
    with Session(audit_engine) as session:
        approved, _, _, _, _ = _proposal(session)
        approved_id = approved.id
    submit_allocation_proposal(factory, approved_id)
    approve_allocation_proposal(factory, approved_id)
    cancel_allocation_proposal(factory, approved_id, reason_code="cancel_approved")
    with Session(audit_engine) as session:
        cancelled = session.get(AllocationProposal, approved_id)
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == approved_id))
        assert cancelled.status is AllocationProposalStatus.CANCELLED
        assert reservation.status is AllocationReservationStatus.CANCELLED
        assert reservation.committed_at is not None and reservation.terminal_at is not None

    with Session(audit_engine) as session:
        expiring, _, _, _, _ = _proposal(session)
        expiring_id = expiring.id
    submit_allocation_proposal(factory, expiring_id)
    with audit_engine.begin() as connection:
        past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        connection.execute(text("UPDATE allocation_proposals SET expires_at=:past WHERE id=:id"), {"past": past, "id": expiring_id})
        connection.execute(text("UPDATE allocation_reservations SET expires_at=:past WHERE proposal_id=:id"),
                           {"past": past, "id": expiring_id})
    with Session(audit_engine) as session:
        pending = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == expiring_id))
        assert pending.status is AllocationReservationStatus.PENDING and pending.expires_at < datetime.now(timezone.utc)
    expired = expire_pending_reservations(factory, expiring_id)
    assert expired is not None and expired.status is AllocationProposalStatus.EXPIRED
    with Session(audit_engine) as session:
        proposal = session.get(AllocationProposal, expiring_id)
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == expiring_id))
        assert proposal.terminal_reason_code is None and reservation.terminal_reason_code is None

    with Session(audit_engine) as session:
        empty, _, _, _, lines = _proposal(session, quantities=())
        empty_id = empty.id
        assert lines == []
    with pytest.raises(InvalidAllocationLineError):
        submit_allocation_proposal(factory, empty_id)
    with Session(audit_engine) as session:
        assert session.get(AllocationProposal, empty_id).status is AllocationProposalStatus.DRAFT


BLANK_REASONS = ("", "   ", "\t", "\n", "\r", " \t\n\r\f\v ", "\u00a0", "\u001c", "\u3000")


@pytest.mark.parametrize("blank", BLANK_REASONS)
def test_terminal_reason_service_rejects_blank_code_and_explanation(blank):
    with pytest.raises(InvalidTerminalReasonError):
        _terminal_reason(blank, None)
    with pytest.raises(InvalidTerminalReasonError):
        _terminal_reason("valid_code", blank)


def test_terminal_reason_service_preserves_valid_historical_values():
    assert _terminal_reason(" legacy_code_42 ", " historical explanation ") == (
        "legacy_code_42", "historical explanation",
    )
    assert _terminal_reason("legacy_code_42", None) == ("legacy_code_42", None)


def test_migrated_reason_checks_match_service_whitespace_policy(audit_engine):
    _alembic(audit_engine, "upgrade", REVISION)
    with audit_engine.begin() as connection:
        _, _, proposal_id, version_id, line_id, _ = _seed_sql(connection)
        reservation_id = uuid4()
        connection.execute(text("""INSERT INTO allocation_reservations
            (id, allocation_line_id, proposal_id, proposal_version_id,
             reserved_quantity_kg, status, expires_at)
            VALUES (:id, :line, :proposal, :version, 1.000, 'PENDING', now() + interval '1 day')"""),
            {"id": reservation_id, "line": line_id, "proposal": proposal_id, "version": version_id})
        proposal_checks = {
            "REJECTED": "ck_allocation_proposals_rejected_metadata",
            "CANCELLED": "ck_allocation_proposals_cancelled_metadata",
            "RELEASED": "ck_allocation_proposals_released_reason",
        }
        proposal_update = """UPDATE allocation_proposals
            SET status=:status, rejected_at=now(), cancelled_at=now(), terminal_reason_code=:reason
            WHERE id=:id"""
        reservation_update = """UPDATE allocation_reservations
            SET status=:status, terminal_at=now(), terminal_reason_code=:reason WHERE id=:id"""
        for blank in BLANK_REASONS:
            for status, constraint in proposal_checks.items():
                _reject(connection, proposal_update,
                        {"id": proposal_id, "status": status, "reason": blank}, constraint)
            for status in ("REJECTED", "CANCELLED"):
                _reject(connection, reservation_update,
                        {"id": reservation_id, "status": status, "reason": blank},
                        "ck_allocation_reservations_rejected_cancelled_reason")
        for valid in ("legacy_code_42", "  legacy_code_42 \t"):
            for status in proposal_checks:
                savepoint = connection.begin_nested()
                try:
                    connection.execute(text(proposal_update),
                                       {"id": proposal_id, "status": status, "reason": valid})
                    assert connection.scalar(text("SELECT terminal_reason_code FROM allocation_proposals WHERE id=:id"),
                                             {"id": proposal_id}) == valid
                finally:
                    savepoint.rollback()
            for status in ("REJECTED", "CANCELLED"):
                savepoint = connection.begin_nested()
                try:
                    connection.execute(text(reservation_update),
                                       {"id": reservation_id, "status": status, "reason": valid})
                    assert connection.scalar(text("SELECT terminal_reason_code FROM allocation_reservations WHERE id=:id"),
                                             {"id": reservation_id}) == valid
                finally:
                    savepoint.rollback()


def test_event_insertion_failure_rolls_back_submission_under_migration(audit_engine):
    _alembic(audit_engine, "upgrade", REVISION)
    with Session(audit_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    def fail_event(_mapper, _connection, target):
        if target.event_type == "proposal_submitted":
            raise RuntimeError("injected event insertion failure")
    event.listen(AllocationEvent, "before_insert", fail_event)
    try:
        with pytest.raises(RuntimeError, match="injected event insertion failure"):
            submit_allocation_proposal(_factory(audit_engine), proposal_id)
    finally:
        event.remove(AllocationEvent, "before_insert", fail_event)
    with Session(audit_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal.status is AllocationProposalStatus.DRAFT
        assert proposal.submitted_at is None and proposal.expires_at is None
        assert not tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert not tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_submitted")))


@pytest.mark.parametrize("winner", ["submission", "line"])
def test_submission_and_line_insert_serialize(audit_engine, winner):
    _alembic(audit_engine, "upgrade", REVISION)
    with Session(audit_engine) as session:
        proposal, version, batch, order, _ = _proposal(session)
        proposal_id, version_id, batch_id, order_id = proposal.id, version.id, batch.id, order.id
    factory = _factory(audit_engine)
    held = threading.Event()
    resume = threading.Event()
    attempted = threading.Event()
    submission_errors = []
    insertion_errors = []
    submission_thread_id = []

    def pause(_connection, _cursor, statement, _parameters, _context, _executemany):
        if submission_thread_id and threading.get_ident() == submission_thread_id[0] and not held.is_set():
            if winner == "line" and "FROM batches" in statement and "FOR UPDATE" in statement:
                held.set()
                assert resume.wait(10)
            elif winner == "submission" and "FROM allocation_proposals" in statement and "FOR UPDATE" in statement:
                held.set()
                assert resume.wait(10)

    def submit():
        submission_thread_id.append(threading.get_ident())
        try:
            submit_allocation_proposal(factory, proposal_id)
        except BaseException as error:
            submission_errors.append(error)

    def insert_line():
        try:
            with audit_engine.begin() as connection:
                attempted.set()
                connection.execute(text("""INSERT INTO allocation_lines
                    (id, proposal_version_id, batch_id, order_id, proposed_quantity_kg)
                    VALUES (:id, :version, :batch, :order, 1.000)"""),
                    {"id": uuid4(), "version": version_id, "batch": batch_id, "order": order_id})
        except BaseException as error:
            insertion_errors.append(error)

    event.listen(audit_engine, "before_cursor_execute" if winner == "line" else "after_cursor_execute", pause)
    submitter = threading.Thread(target=submit)
    inserter = threading.Thread(target=insert_line)
    submitter.start()
    try:
        assert held.wait(10)
        inserter.start()
        assert attempted.wait(10)
        if winner == "line":
            inserter.join(10)
            assert not inserter.is_alive() and not insertion_errors
        resume.set()
        submitter.join(10)
        inserter.join(10)
        assert not submitter.is_alive() and not inserter.is_alive()
    finally:
        resume.set()
        event.remove(audit_engine, "before_cursor_execute" if winner == "line" else "after_cursor_execute", pause)
    with Session(audit_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        line_count = session.scalar(select(text("count(*)")).select_from(AllocationLine).where(AllocationLine.proposal_version_id == version_id))
        if winner == "line":
            assert len(submission_errors) == 1 and isinstance(submission_errors[0], ProposalTargetsChangedError)
            assert proposal.status is AllocationProposalStatus.DRAFT and line_count == 2
        else:
            assert not submission_errors
            assert len(insertion_errors) == 1 and isinstance(insertion_errors[0], IntegrityError)
            assert proposal.status is AllocationProposalStatus.PENDING_APPROVAL and line_count == 1
