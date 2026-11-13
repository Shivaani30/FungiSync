from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
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
from app.reservation_service import (
    InvalidAllocationLineError,
    InvalidProposalVersionError,
    MissingReferencedRecordError,
    ProposalNotFoundError,
    ProposalTargetsChangedError,
    _line_fingerprint,
    _records_by_id,
    validate_proposal_capacity,
)
from app.reservation_transaction import MissingLockedRecordsError, lock_allocation_lines


def _factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _proposal(
    session: Session,
    *,
    batch_quantities: tuple[Decimal, ...] = (Decimal("10.000"),),
    order_quantities: tuple[Decimal, ...] = (Decimal("10.000"),),
    lines: tuple[tuple[int, int, Decimal], ...] = ((0, 0, Decimal("1.000")),),
) -> tuple[AllocationProposal, AllocationProposalVersion, list[Batch], list[Order], list[AllocationLine]]:
    batches: list[Batch] = []
    orders: list[Order] = []
    for index, quantity in enumerate(batch_quantities):
        grower = Grower(name=f"Service grower {uuid4()}", phone=f"9{index:09d}", location="Pune")
        session.add(grower)
        session.flush()
        batch = Batch(grower_id=grower.id, species="oyster", expected_kg=quantity, confirmed_kg=quantity, harvest_date=date(2026, 11, 10))
        session.add(batch)
        batches.append(batch)
    for index, quantity in enumerate(order_quantities):
        buyer = Buyer(name=f"Service buyer {uuid4()}", location="Pune", contact=f"{uuid4()}@example.invalid")
        session.add(buyer)
        session.flush()
        order = Order(buyer_id=buyer.id, species="oyster", quantity_kg=quantity, price_limit=Decimal("100.00"), delivery_date=date(2026, 11, 11))
        session.add(order)
        orders.append(order)
    proposal = AllocationProposal()
    session.add(proposal)
    session.flush()
    version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
    session.add(version)
    session.flush()
    allocation_lines = [
        AllocationLine(proposal_version_id=version.id, batch_id=batches[batch_index].id, order_id=orders[order_index].id, proposed_quantity_kg=quantity)
        for batch_index, order_index, quantity in lines
    ]
    session.add_all(allocation_lines)
    session.commit()
    return proposal, version, batches, orders, allocation_lines


def _supporting_reservation(
    session: Session,
    batch: Batch,
    order: Order,
    quantity: Decimal,
    status: AllocationReservationStatus,
) -> AllocationReservation:
    proposal = AllocationProposal()
    session.add(proposal)
    session.flush()
    version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
    session.add(version)
    session.flush()
    line = AllocationLine(proposal_version_id=version.id, batch_id=batch.id, order_id=order.id, proposed_quantity_kg=quantity)
    session.add(line)
    session.flush()
    now = datetime.now(timezone.utc)
    reservation = AllocationReservation(
        allocation_line_id=line.id,
        proposal_id=proposal.id,
        proposal_version_id=version.id,
        reserved_quantity_kg=quantity,
        status=status,
        expires_at=now + timedelta(days=1) if status is AllocationReservationStatus.PENDING else None,
        committed_at=now if status in {AllocationReservationStatus.COMMITTED, AllocationReservationStatus.RELEASED} else None,
        terminal_at=now if status in {AllocationReservationStatus.REJECTED, AllocationReservationStatus.CANCELLED, AllocationReservationStatus.EXPIRED, AllocationReservationStatus.RELEASED} else None,
        terminal_reason_code="test_terminal_state"
        if status in {AllocationReservationStatus.REJECTED, AllocationReservationStatus.CANCELLED}
        else None,
    )
    session.add(reservation)
    session.commit()
    return reservation


def test_valid_proposal_returns_decimal_capacity_without_writes(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, batches, orders, _ = _proposal(session)
        proposal_id, version_id, batch_id, order_id, original_status = proposal.id, version.id, batches[0].id, orders[0].id, proposal.status
    result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    assert result.capacity_valid
    assert result.proposal_version_id == version_id
    assert result.batches == (result.batches[0],)
    assert result.batches[0].proposed_quantity_kg == Decimal("1.000")
    assert result.batches[0].available_quantity_kg == Decimal("10.000")
    assert result.orders[0].remaining_quantity_kg == Decimal("10.000")
    assert result.effective_at.tzinfo is not None
    with Session(test_engine) as session:
        assert session.scalar(select(func.count(AllocationReservation.id))) == 0
        assert session.get(AllocationProposal, proposal_id).status == original_status  # type: ignore[union-attr]
        assert session.get(Batch, batch_id).confirmed_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert session.get(Order, order_id).quantity_kg == Decimal("10.000")  # type: ignore[union-attr]


def test_insufficient_batch_and_order_return_deterministic_conflicts(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batches, orders, _ = _proposal(
            session, batch_quantities=(Decimal("3.000"),), order_quantities=(Decimal("3.000"),),
            lines=((0, 0, Decimal("4.000")),),
        )
        proposal_id, batch_id, order_id = proposal.id, batches[0].id, orders[0].id
    result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    assert not result.capacity_valid
    assert [(item.resource_type, item.resource_id) for item in result.conflicts] == [
        ("batch", batch_id), ("order", order_id),
    ]


def test_shared_batch_and_order_lines_are_aggregated(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(
            session, batch_quantities=(Decimal("5.000"),), order_quantities=(Decimal("5.000"),),
            lines=((0, 0, Decimal("2.111")), (0, 0, Decimal("3.222"))),
        )
        proposal_id = proposal.id
    result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    assert not result.capacity_valid
    assert result.batches[0].proposed_quantity_kg == Decimal("5.333")
    assert result.orders[0].proposed_quantity_kg == Decimal("5.333")


def test_active_reservations_reduce_capacity_but_terminal_and_expired_pending_do_not(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batches, orders, _ = _proposal(session, lines=((0, 0, Decimal("4.000")),))
        _supporting_reservation(session, batches[0], orders[0], Decimal("3.000"), AllocationReservationStatus.COMMITTED)
        expired = _supporting_reservation(session, batches[0], orders[0], Decimal("2.000"), AllocationReservationStatus.PENDING)
        expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(expired)
        session.commit()
        _supporting_reservation(session, batches[0], orders[0], Decimal("2.000"), AllocationReservationStatus.CANCELLED)
        proposal_id = proposal.id
    result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    assert result.capacity_valid
    assert result.batches[0].available_quantity_kg == Decimal("7.000")
    assert result.orders[0].remaining_quantity_kg == Decimal("7.000")


def test_multiple_reservations_and_decimal_precision_are_preserved(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batches, orders, _ = _proposal(session, lines=((0, 0, Decimal("1.111")),))
        _supporting_reservation(session, batches[0], orders[0], Decimal("2.222"), AllocationReservationStatus.COMMITTED)
        _supporting_reservation(session, batches[0], orders[0], Decimal("3.333"), AllocationReservationStatus.PENDING)
        proposal_id = proposal.id
    result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    assert result.batches[0].available_quantity_kg == Decimal("4.445")
    assert result.orders[0].remaining_quantity_kg == Decimal("4.445")


def test_missing_proposal_and_version_roll_back_without_writes(test_engine) -> None:
    with pytest.raises(ProposalNotFoundError):
        validate_proposal_capacity(_factory(test_engine), uuid4())
    with Session(test_engine) as session:
        proposal = AllocationProposal()
        session.add(proposal)
        session.commit()
        proposal_id = proposal.id
    with pytest.raises(InvalidProposalVersionError):
        validate_proposal_capacity(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        assert session.get(AllocationProposal, proposal_id) is not None
        assert session.scalar(select(func.count(AllocationReservation.id))) == 0


def test_invalid_lines_and_missing_references_are_rejected_safely(test_engine) -> None:
    line = AllocationLine(id=uuid4(), proposal_version_id=uuid4(), batch_id=uuid4(), order_id=uuid4(), proposed_quantity_kg=Decimal("0"))
    with pytest.raises(InvalidAllocationLineError):
        _line_fingerprint((line,), line.proposal_version_id)
    with pytest.raises(MissingReferencedRecordError):
        _records_by_id((), (uuid4(),), "batch")


def test_service_uses_staged_parent_then_reservation_locks(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batches, orders, _ = _proposal(session)
        _supporting_reservation(session, batches[0], orders[0], Decimal("1.000"), AllocationReservationStatus.COMMITTED)
        proposal_id = proposal.id
    lock_statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FOR UPDATE" in statement:
            lock_statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        result = validate_proposal_capacity(_factory(test_engine), proposal_id)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert result.capacity_valid
    assert [
        next(name for name in ("batches", "orders", "allocation_proposals", "allocation_lines", "allocation_reservations") if name in statement)
        for statement in lock_statements
    ] == ["batches", "orders", "allocation_proposals", "allocation_lines", "allocation_reservations"]
    assert "FROM allocation_reservations" in lock_statements[-1]
    assert all(name not in lock_statements[-1] for name in ("batches", "orders", "allocation_proposals"))


def test_allocation_line_lock_handles_empty_and_missing_ids(test_engine) -> None:
    with Session(test_engine) as session:
        assert lock_allocation_lines(session, ()) == ()
        with pytest.raises(MissingLockedRecordsError, match="Required allocation line records do not exist"):
            lock_allocation_lines(session, (uuid4(),))


def test_line_update_blocks_while_validator_holds_line_lock(test_engine) -> None:
    with Session(test_engine) as setup:
        proposal, _, _, _, lines = _proposal(setup, lines=((0, 0, Decimal("1.000")),))
        proposal_id, line_id = proposal.id, lines[0].id
    line_locked = threading.Event()
    release_validator = threading.Event()
    validator_done = threading.Event()
    updater_ready = threading.Event()
    updater_start = threading.Event()
    updater_done = threading.Event()
    validator: dict[str, object] = {}
    updater: dict[str, object] = {}
    results: list[object] = []
    errors: list[BaseException] = []

    def pause_at_line_lock(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FROM allocation_lines" in statement and "FOR UPDATE" in statement:
            validator["pid"] = connection.connection.driver_connection.info.backend_pid
            line_locked.set()
            if not release_validator.wait(timeout=5):
                raise RuntimeError("validator was not released after line lock")

    def validate() -> None:
        try:
            results.append(validate_proposal_capacity(_factory(test_engine), proposal_id))
        except BaseException as exc:
            errors.append(exc)
        finally:
            validator_done.set()

    def update_line() -> None:
        try:
            with Session(test_engine) as session:
                updater["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
                updater_ready.set()
                if not updater_start.wait(timeout=5):
                    raise RuntimeError("updater was not released")
                session.execute(text("UPDATE allocation_lines SET proposed_quantity_kg = 2.000 WHERE id = :id"), {"id": line_id})
                session.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            updater_done.set()

    event.listen(test_engine, "after_cursor_execute", pause_at_line_lock)
    validation_thread = threading.Thread(target=validate)
    update_thread = threading.Thread(target=update_line)
    try:
        validation_thread.start()
        assert line_locked.wait(timeout=5)
        update_thread.start()
        assert updater_ready.wait(timeout=5)
        updater_start.set()
        with Session(test_engine) as monitor:
            deadline = monotonic() + 5
            observed = []
            while monotonic() < deadline:
                activity = monitor.execute(text("""
                    SELECT pg_blocking_pids(:pid), wait_event_type
                    FROM pg_stat_activity WHERE pid = :pid
                """), {"pid": updater["pid"]}).one_or_none()
                observed.append(activity)
                if activity is not None and validator["pid"] in activity[0] and activity[1] == "Lock":
                    break
                if updater_done.wait(0.05):
                    pytest.fail(f"line update completed before blocking: {observed!r}")
            else:
                pytest.fail(f"line update did not block on validator: {observed!r}")
        release_validator.set()
        validation_thread.join(timeout=5)
        update_thread.join(timeout=5)
        assert not validation_thread.is_alive() and not update_thread.is_alive()
        assert not errors
        assert results[0].batches[0].proposed_quantity_kg == Decimal("1.000")
    finally:
        release_validator.set()
        updater_start.set()
        if validation_thread.ident is not None:
            validation_thread.join(timeout=5)
        if update_thread.ident is not None:
            update_thread.join(timeout=5)
        event.remove(test_engine, "after_cursor_execute", pause_at_line_lock)
    assert validator_done.is_set() and updater_done.is_set()
    with Session(test_engine) as session:
        assert session.get(AllocationLine, line_id).proposed_quantity_kg == Decimal("2.000")  # type: ignore[union-attr]


def test_line_change_committed_before_line_lock_is_detected(test_engine) -> None:
    with Session(test_engine) as setup:
        proposal, _, _, _, lines = _proposal(setup, lines=((0, 0, Decimal("1.000")),))
        proposal_id, line_id = proposal.id, lines[0].id
    writer = Session(test_engine)
    line_lock_attempt = threading.Event()
    validator_done = threading.Event()
    validator: dict[str, object] = {}
    errors: list[BaseException] = []

    def observe_line_lock(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FROM allocation_lines" in statement and "FOR UPDATE" in statement:
            validator["pid"] = connection.connection.driver_connection.info.backend_pid
            line_lock_attempt.set()

    def validate() -> None:
        try:
            validate_proposal_capacity(_factory(test_engine), proposal_id)
        except BaseException as exc:
            errors.append(exc)
        finally:
            validator_done.set()

    event.listen(test_engine, "before_cursor_execute", observe_line_lock)
    thread = threading.Thread(target=validate)
    try:
        writer.execute(text("UPDATE allocation_lines SET proposed_quantity_kg = 2.000 WHERE id = :id"), {"id": line_id})
        writer_pid = writer.scalar(text("SELECT pg_backend_pid()"))
        thread.start()
        assert line_lock_attempt.wait(timeout=5)
        deadline = monotonic() + 5
        observed = []
        while monotonic() < deadline:
            activity = writer.execute(text("""
                SELECT pg_blocking_pids(:pid), wait_event_type
                FROM pg_stat_activity WHERE pid = :pid
            """), {"pid": validator["pid"]}).one_or_none()
            observed.append(activity)
            if activity is not None and writer_pid in activity[0] and activity[1] == "Lock":
                break
            if validator_done.wait(0.05):
                pytest.fail(f"validator completed before line-lock wait: {observed!r}")
        else:
            pytest.fail(f"validator did not block on writer line lock: {observed!r}")
        writer.commit()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ProposalTargetsChangedError)
    finally:
        if writer.in_transaction():
            writer.rollback()
        if thread.ident is not None:
            thread.join(timeout=5)
        writer.close()
        event.remove(test_engine, "before_cursor_execute", observe_line_lock)
    assert validator_done.is_set()
