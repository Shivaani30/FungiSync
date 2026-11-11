from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    AllocationLine,
    AllocationProposal,
    AllocationProposalVersion,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    Buyer,
    Grower,
    Order,
)
from app.reservation_transaction import (
    MissingLockedRecordsError,
    effective_timestamp,
    is_retryable_postgres_failure,
    lock_reservation_targets,
    postgres_sqlstate,
    reread_locked_targets,
    run_reservation_transaction,
)


def _targets(session: Session, *, count: int = 1) -> tuple[list[Batch], list[Order], list[AllocationProposal], list[AllocationReservation]]:
    batches: list[Batch] = []
    orders: list[Order] = []
    proposals: list[AllocationProposal] = []
    reservations: list[AllocationReservation] = []
    for number in range(count):
        grower = Grower(name=f"Transaction grower {number}", phone=f"9999999{number:03d}", location="Pune")
        buyer = Buyer(name=f"Transaction buyer {number}", location="Pune", contact=f"buyer-{number}@example.invalid")
        session.add_all((grower, buyer))
        session.flush()
        batch = Batch(grower_id=grower.id, species="oyster", expected_kg=Decimal("10.000"), confirmed_kg=Decimal("10.000"), harvest_date=date(2026, 11, 10))
        order = Order(buyer_id=buyer.id, species="oyster", quantity_kg=Decimal("10.000"), price_limit=Decimal("100.00"), delivery_date=date(2026, 11, 11))
        proposal = AllocationProposal()
        session.add_all((batch, order, proposal))
        session.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        session.add(version)
        session.flush()
        line = AllocationLine(proposal_version_id=version.id, batch_id=batch.id, order_id=order.id, proposed_quantity_kg=Decimal("1.000"))
        session.add(line)
        session.flush()
        reservation = AllocationReservation(
            allocation_line_id=line.id,
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reserved_quantity_kg=Decimal("1.000"),
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        session.add(reservation)
        batches.append(batch)
        orders.append(order)
        proposals.append(proposal)
        reservations.append(reservation)
    session.commit()
    return batches, orders, proposals, reservations


class _TrackingSession(Session):
    closed_instances: list["_TrackingSession"] = []

    def close(self) -> None:
        type(self).closed_instances.append(self)
        super().close()


def test_factory_owned_transaction_does_not_commit_caller_work_and_closes(test_engine) -> None:
    _TrackingSession.closed_instances.clear()
    factory = sessionmaker(bind=test_engine, class_=_TrackingSession, autoflush=False, autocommit=False)
    caller = Session(test_engine)
    caller.add(Grower(name="Caller-owned", phone="9999999999", location="Pune"))

    created_id = run_reservation_transaction(
        factory,
        lambda session: _create_grower(session, "Factory-owned", "9999999998"),
    )

    with Session(test_engine) as verifier:
        assert verifier.get(Grower, created_id) is not None
        assert verifier.scalar(select(Grower).where(Grower.name == "Caller-owned")) is None
    assert caller.new
    assert _TrackingSession.closed_instances
    caller.rollback()
    caller.close()


def _create_grower(session: Session, name: str, phone: str) -> UUID:
    grower = Grower(name=name, phone=phone, location="Pune")
    session.add(grower)
    session.flush()
    return grower.id


def test_factory_owned_transaction_rolls_back_everything_on_exception(test_engine) -> None:
    _TrackingSession.closed_instances.clear()
    factory = sessionmaker(bind=test_engine, class_=_TrackingSession, autoflush=False, autocommit=False)

    def failing_operation(session: Session) -> None:
        _create_grower(session, "Rolled back", "9999999997")
        raise RuntimeError("forced failure")

    with pytest.raises(RuntimeError, match="forced failure"):
        run_reservation_transaction(factory, failing_operation)
    with Session(test_engine) as verifier:
        assert verifier.scalar(select(Grower).where(Grower.name == "Rolled back")) is None
    assert _TrackingSession.closed_instances


def test_locks_follow_global_order_sort_ids_and_reject_missing_records(test_engine) -> None:
    with Session(test_engine) as session:
        batches, orders, proposals, reservations = _targets(session, count=2)
        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
            if "FOR UPDATE" in statement:
                statements.append(statement)

        event.listen(test_engine, "before_cursor_execute", capture)
        try:
            locked = lock_reservation_targets(
                session,
                batch_ids=(batches[1].id, batches[0].id),
                order_ids=(orders[1].id, orders[0].id),
                proposal_ids=(proposals[1].id, proposals[0].id),
                reservation_ids=(reservations[1].id, reservations[0].id),
            )
        finally:
            event.remove(test_engine, "before_cursor_execute", capture)
        assert [item.id for item in locked.batches] == sorted(item.id for item in batches)
        assert [item.id for item in locked.orders] == sorted(item.id for item in orders)
        assert [item.id for item in locked.proposals] == sorted(item.id for item in proposals)
        assert [item.id for item in locked.reservations] == sorted(item.id for item in reservations)
        assert [next(name for name in ("batches", "orders", "allocation_proposals", "allocation_reservations") if name in statement) for statement in statements] == ["batches", "orders", "allocation_proposals", "allocation_reservations"]
        assert all("ORDER BY" in statement for statement in statements)

        assert lock_reservation_targets(session).batches == ()
        with pytest.raises(MissingLockedRecordsError, match="Required batch records do not exist"):
            lock_reservation_targets(session, batch_ids=(uuid4(),))


def test_reread_after_locking_refreshes_previously_loaded_state(test_engine) -> None:
    with Session(test_engine) as setup:
        batch_id = _targets(setup)[0][0].id
    first = Session(test_engine)
    try:
        stale = first.get(Batch, batch_id)
        assert stale is not None and stale.confirmed_kg == Decimal("10.000")
        with Session(test_engine) as writer:
            writer.get(Batch, batch_id).confirmed_kg = Decimal("7.000")  # type: ignore[union-attr]
            writer.commit()
        lock_reservation_targets(first, batch_ids=(batch_id,))
        refreshed = reread_locked_targets(first, batch_ids=(batch_id,))
        assert refreshed.batches[0].confirmed_kg == Decimal("7.000")
        assert stale.confirmed_kg == Decimal("7.000")
    finally:
        first.rollback()
        first.close()


def test_effective_timestamp_uses_postgresql_clock_after_locks(test_engine) -> None:
    with Session(test_engine) as session:
        batch_id = _targets(session)[0][0].id
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        with Session(test_engine) as session:
            lock_reservation_targets(session, batch_ids=(batch_id,))
            captured = effective_timestamp(session)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert captured.tzinfo is not None and captured.utcoffset() is not None
    lock_index = next(index for index, statement in enumerate(statements) if "FOR UPDATE" in statement)
    clock_index = next(index for index, statement in enumerate(statements) if "clock_timestamp" in statement)
    assert lock_index < clock_index


def test_parent_lock_blocks_competing_lock_then_allows_it_after_commit(test_engine) -> None:
    with Session(test_engine) as setup:
        batch_id = _targets(setup)[0][0].id
    controller = Session(test_engine)
    worker_ready = threading.Event()
    worker_start = threading.Event()
    worker_done = threading.Event()
    worker: dict[str, object] = {}
    errors: list[BaseException] = []

    def competing_lock() -> None:
        try:
            with Session(test_engine) as session:
                worker["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
                worker_ready.set()
                if not worker_start.wait(timeout=5):
                    raise RuntimeError("worker was not released")
                lock_reservation_targets(session, batch_ids=(batch_id,))
                session.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            worker_done.set()

    thread = threading.Thread(target=competing_lock)
    try:
        lock_reservation_targets(controller, batch_ids=(batch_id,))
        blocker_pid = controller.scalar(text("SELECT pg_backend_pid()"))
        thread.start()
        assert worker_ready.wait(timeout=5)
        worker_start.set()
        deadline = monotonic() + 5
        observed = []
        while monotonic() < deadline:
            activity = controller.execute(text("""
                SELECT pg_blocking_pids(:pid), wait_event_type
                FROM pg_stat_activity WHERE pid = :pid
            """), {"pid": worker["pid"]}).one_or_none()
            observed.append(activity)
            if activity is not None and blocker_pid in activity[0] and activity[1] == "Lock":
                break
            if worker_done.wait(0.05):
                pytest.fail(f"worker completed before PostgreSQL lock wait: {observed!r}")
        else:
            pytest.fail(f"worker did not block on controller backend {blocker_pid}: {observed!r}")
        controller.commit()
        if thread.ident is not None:
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
    finally:
        worker_start.set()
        if controller.in_transaction():
            controller.rollback()
        thread.join(timeout=5)
        controller.close()
    assert worker_done.is_set()


def test_post_parent_lock_reservation_discovery_sees_newly_committed_reservation(test_engine) -> None:
    """A stale pre-lock reservation list is replaced while parent locks are held."""
    with Session(test_engine) as setup:
        batches, orders, proposals, reservations = _targets(setup)
        extra_line = AllocationLine(
            proposal_version_id=reservations[0].proposal_version_id,
            batch_id=batches[0].id,
            order_id=orders[0].id,
            proposed_quantity_kg=Decimal("2.000"),
        )
        setup.add(extra_line)
        setup.commit()
        batch_id, order_id, proposal_id = batches[0].id, orders[0].id, proposals[0].id
        initial_reservation_id, extra_line_id, version_id = (
            reservations[0].id,
            extra_line.id,
            reservations[0].proposal_version_id,
        )

    # This is the deliberately stale discovery a future operation performs
    # before taking parent locks.
    with Session(test_engine) as discoverer:
        stale_reservation_ids = tuple(discoverer.scalars(
            select(AllocationReservation.id).where(AllocationReservation.proposal_id == proposal_id)
        ))
    assert stale_reservation_ids == (initial_reservation_id,)

    writer = Session(test_engine)
    controller_ready = threading.Event()
    controller_start = threading.Event()
    controller_done = threading.Event()
    controller: dict[str, object] = {}
    errors: list[BaseException] = []
    controller_lock_statements: list[str] = []

    def capture(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if connection.info.get("reservation_transaction_role") == "controller" and "FOR UPDATE" in statement:
            controller_lock_statements.append(statement)

    def lock_then_rediscover() -> None:
        try:
            with Session(test_engine) as session:
                session.connection().info["reservation_transaction_role"] = "controller"
                controller["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
                controller_ready.set()
                if not controller_start.wait(timeout=5):
                    raise RuntimeError("controller was not released")
                lock_reservation_targets(
                    session,
                    batch_ids=(batch_id,),
                    order_ids=(order_id,),
                    proposal_ids=(proposal_id,),
                )
                discovered_after_parent_locks = tuple(session.scalars(
                    select(AllocationReservation.id)
                    .where(AllocationReservation.proposal_id == proposal_id)
                    .order_by(AllocationReservation.id.asc())
                ))
                reservation_locks = lock_reservation_targets(
                    session,
                    reservation_ids=discovered_after_parent_locks,
                )
                refreshed = reread_locked_targets(
                    session,
                    batch_ids=(batch_id,),
                    order_ids=(order_id,),
                    proposal_ids=(proposal_id,),
                    reservation_ids=discovered_after_parent_locks,
                )
                controller["discovered_ids"] = discovered_after_parent_locks
                controller["locked_ids"] = tuple(item.id for item in reservation_locks.reservations)
                controller["reread_ids"] = tuple(item.id for item in refreshed.reservations)
                session.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            controller_done.set()

    thread = threading.Thread(target=lock_then_rediscover)
    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        lock_reservation_targets(
            writer,
            batch_ids=(batch_id,),
            order_ids=(order_id,),
            proposal_ids=(proposal_id,),
        )
        writer_pid = writer.scalar(text("SELECT pg_backend_pid()"))
        thread.start()
        assert controller_ready.wait(timeout=5)
        controller_start.set()
        deadline = monotonic() + 5
        observed = []
        while monotonic() < deadline:
            activity = writer.execute(text("""
                SELECT pg_blocking_pids(:pid), wait_event_type
                FROM pg_stat_activity WHERE pid = :pid
            """), {"pid": controller["pid"]}).one_or_none()
            observed.append(activity)
            if activity is not None and writer_pid in activity[0] and activity[1] == "Lock":
                break
            if controller_done.wait(0.05):
                pytest.fail(f"controller completed before PostgreSQL parent-lock wait: {observed!r}")
        else:
            pytest.fail(f"controller did not block on writer backend {writer_pid}: {observed!r}")

        new_reservation = AllocationReservation(
            allocation_line_id=extra_line_id,
            proposal_id=proposal_id,
            proposal_version_id=version_id,
            reserved_quantity_kg=Decimal("2.000"),
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        writer.add(new_reservation)
        writer.commit()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        expected_ids = tuple(sorted((initial_reservation_id, new_reservation.id)))
        assert controller["discovered_ids"] == expected_ids
        assert controller["locked_ids"] == expected_ids
        assert controller["reread_ids"] == expected_ids
        assert [
            next(name for name in ("batches", "orders", "allocation_proposals", "allocation_reservations") if name in statement)
            for statement in controller_lock_statements
        ] == ["batches", "orders", "allocation_proposals", "allocation_reservations"]
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
        controller_start.set()
        if writer.in_transaction():
            writer.rollback()
        if thread.ident is not None:
            thread.join(timeout=5)
        writer.close()
    assert controller_done.is_set()


@pytest.mark.parametrize("code", ["40001", "40P01", "55P03"])
def test_postgres_failure_classification_is_specific(code: str) -> None:
    class DriverError(Exception):
        sqlstate = code

    error = OperationalError("statement", {}, DriverError())
    assert postgres_sqlstate(error) == code
    assert is_retryable_postgres_failure(error)
    assert not is_retryable_postgres_failure(RuntimeError("not a database failure"))
