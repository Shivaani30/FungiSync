from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

import app.reservation_service as reservation_service
from app.core.config import Settings
from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationOperationIdempotency,
    AllocationProposal,
    AllocationProposalStatus,
    AllocationProposalVersion,
    AllocationQualityGrade,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    BatchStatus,
    Buyer,
    Grower,
    Order,
    OrderStatus,
)
from app.quantity_accounting import reservation_consumes_capacity
from app.reservation_service import (
    ExpiredReservationError,
    IneligibleReferencedRecordError,
    InsufficientBatchCapacityError,
    InsufficientOrderCapacityError,
    InvalidAllocationLineError,
    InvalidReservationError,
    InvalidReservationStateError,
    InvalidTerminalReasonError,
    IdempotencyConflictError,
    MissingProposalReservationsError,
    ProposalNotAwaitingApprovalError,
    ProposalNotSubmittableError,
    ProposalTargetsChangedError,
    ProposalNotTerminableError,
    approve_allocation_proposal,
    cancel_allocation_proposal,
    expire_pending_reservations,
    reject_allocation_proposal,
    release_committed_reservations,
    submit_allocation_proposal,
)


def _factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _proposal(
    session: Session,
    *,
    batch_quantity: Decimal = Decimal("10.000"),
    order_quantity: Decimal = Decimal("10.000"),
    quantities: tuple[Decimal, ...] = (Decimal("1.000"),),
) -> tuple[AllocationProposal, AllocationProposalVersion, Batch, Order, list[AllocationLine]]:
    grower = Grower(name=f"Submit grower {uuid4()}", phone="9999999999", location="Pune")
    buyer = Buyer(name=f"Submit buyer {uuid4()}", location="Pune", contact=f"{uuid4()}@example.invalid")
    session.add_all((grower, buyer))
    session.flush()
    batch = Batch(
        grower_id=grower.id, species="oyster", expected_kg=batch_quantity,
        confirmed_kg=batch_quantity, harvest_date=date(2026, 11, 10),
        best_before_date=date(2026, 11, 12), asking_price_per_kg=Decimal("100.00"),
        allocation_quality_grade=AllocationQualityGrade.PREMIUM, status=BatchStatus.CONFIRMED,
    )
    order = Order(
        buyer_id=buyer.id, species="oyster", quantity_kg=order_quantity,
        price_limit=Decimal("100.00"), delivery_date=date(2026, 11, 11),
        allocation_required_quality_grade=AllocationQualityGrade.PREMIUM, status=OrderStatus.PENDING,
    )
    proposal = AllocationProposal()
    session.add_all((batch, order, proposal))
    session.flush()
    version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
    session.add(version)
    session.flush()
    lines = [
        AllocationLine(proposal_version_id=version.id, batch_id=batch.id, order_id=order.id, proposed_quantity_kg=quantity)
        for quantity in quantities
    ]
    session.add_all(lines)
    session.commit()
    for record in (proposal, version, batch, order, *lines):
        session.refresh(record)
    return proposal, version, batch, order, lines


def _reservation(session: Session, batch: Batch, order: Order, quantity: Decimal, status: AllocationReservationStatus, *, expires_at: datetime | None = None) -> None:
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
    session.add(AllocationReservation(
        allocation_line_id=line.id, proposal_id=proposal.id, proposal_version_id=version.id,
        reserved_quantity_kg=quantity, status=status,
        expires_at=expires_at if status is AllocationReservationStatus.PENDING else None,
        committed_at=now if status is AllocationReservationStatus.COMMITTED else None,
    ))
    session.commit()


def test_pending_ttl_setting_is_required_and_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESERVATION_PENDING_TTL_SECONDS", raising=False)
    with pytest.raises(Exception, match="reservation_pending_ttl_seconds"):
        Settings(_env_file=None, database_url="postgresql+psycopg://example/db")
    with pytest.raises(Exception, match="greater than 0"):
        Settings(_env_file=None, database_url="postgresql+psycopg://example/db", reservation_pending_ttl_seconds=0)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://example/db", reservation_pending_ttl_seconds=3600)
    assert settings.reservation_pending_ttl_seconds == 3600


def test_submit_creates_pending_reservations_transitions_and_records_event(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, _, _, lines = _proposal(session, quantities=(Decimal("1.111"), Decimal("2.222")))
        proposal_id = proposal.id
    result = submit_allocation_proposal(_factory(test_engine), proposal_id)
    assert result.proposal_version_id == version.id
    assert result.expires_at == result.effective_at + timedelta(seconds=3600)
    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        reservations = list(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id).order_by(AllocationReservation.allocation_line_id)))
        submission_event = session.get(AllocationEvent, result.event_id)
        assert stored is not None and stored.status is AllocationProposalStatus.PENDING_APPROVAL
        assert stored.submitted_at == result.effective_at and stored.expires_at == result.expires_at
        assert stored.approved_at is None and stored.rejected_at is None and stored.cancelled_at is None
        assert len(reservations) == len(lines) == 2
        assert {reservation.allocation_line_id: reservation.reserved_quantity_kg for reservation in reservations} == {line.id: line.proposed_quantity_kg for line in lines}
        assert all(reservation.status is AllocationReservationStatus.PENDING for reservation in reservations)
        assert {reservation.expires_at for reservation in reservations} == {result.expires_at}
        assert submission_event is not None and submission_event.event_type == "proposal_submitted"
        assert submission_event.details == {"reservation_count": 2, "expires_at": result.expires_at.isoformat()}


def test_zero_line_draft_cannot_be_submitted(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, _, _, lines = _proposal(session, quantities=())
        proposal_id = proposal.id
        assert lines == []

    with pytest.raises(InvalidAllocationLineError, match="cannot be submitted without allocation lines"):
        submit_allocation_proposal(_factory(test_engine), proposal_id)

    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        assert stored is not None and stored.status is AllocationProposalStatus.DRAFT
        assert stored.submitted_at is None and stored.expires_at is None
        assert session.get(AllocationProposalVersion, version.id) is not None
        assert not tuple(session.scalars(select(AllocationLine).where(AllocationLine.proposal_version_id == version.id)))
        assert not tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert not tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_submitted",
        )))
        assert not tuple(session.scalars(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.proposal_id == proposal_id,
        )))


def test_submit_rejects_insufficient_batch_or_order_capacity(test_engine) -> None:
    with Session(test_engine) as session:
        batch_proposal, _, _, _, _ = _proposal(session, batch_quantity=Decimal("1.000"), quantities=(Decimal("1.001"),))
        batch_proposal_id = batch_proposal.id
        order_proposal, _, _, _, _ = _proposal(session, order_quantity=Decimal("1.000"), quantities=(Decimal("1.001"),))
        order_proposal_id = order_proposal.id
    with pytest.raises(InsufficientBatchCapacityError):
        submit_allocation_proposal(_factory(test_engine), batch_proposal_id)
    with pytest.raises(InsufficientOrderCapacityError):
        submit_allocation_proposal(_factory(test_engine), order_proposal_id)


@pytest.mark.parametrize("status", [AllocationReservationStatus.PENDING, AllocationReservationStatus.COMMITTED])
def test_active_reservations_consume_submission_capacity(test_engine, status: AllocationReservationStatus) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("8.000"),))
        proposal_id = proposal.id
        _reservation(session, batch, order, Decimal("3.000"), status, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(InsufficientBatchCapacityError):
        submit_allocation_proposal(_factory(test_engine), proposal_id)


def test_expired_pending_does_not_consume_and_boundary_is_not_active(test_engine) -> None:
    boundary = datetime.now(timezone.utc)
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("8.000"),))
        proposal_id = proposal.id
        _reservation(session, batch, order, Decimal("3.000"), AllocationReservationStatus.PENDING, expires_at=boundary - timedelta(days=1))
        sample = session.scalar(select(AllocationReservation).order_by(AllocationReservation.created_at.desc()))
        assert sample is not None and not reservation_consumes_capacity(sample, boundary)
    submit_allocation_proposal(_factory(test_engine), proposal_id)


def test_submit_rejects_ineligible_record_and_repeat_submission(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, _, _ = _proposal(session)
        ineligible_proposal_id = proposal.id
        batch.status = BatchStatus.CANCELLED
        session.commit()
    with pytest.raises(IneligibleReferencedRecordError):
        submit_allocation_proposal(_factory(test_engine), ineligible_proposal_id)
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with pytest.raises(ProposalNotSubmittableError):
        submit_allocation_proposal(_factory(test_engine), proposal_id)


def test_failed_reservation_insert_rolls_back_the_transition(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id

    def fail_insert(_mapper, _connection, _target) -> None:
        raise RuntimeError("forced reservation insert failure")

    event.listen(AllocationReservation, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="forced reservation insert failure"):
            submit_allocation_proposal(_factory(test_engine), proposal_id)
    finally:
        event.remove(AllocationReservation, "before_insert", fail_insert)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.DRAFT
        assert proposal.submitted_at is None and proposal.expires_at is None
        assert list(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))) == []


def test_concurrent_same_proposal_creates_reservations_once(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    errors: list[BaseException] = []
    results: list[object] = []
    start = threading.Event()

    def submit() -> None:
        start.wait(timeout=5)
        try:
            results.append(submit_allocation_proposal(_factory(test_engine), proposal_id))
        except BaseException as error:
            errors.append(error)

    first, second = threading.Thread(target=submit), threading.Thread(target=submit)
    first.start(); second.start(); start.set()
    first.join(timeout=5); second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], ProposalNotSubmittableError)
    with Session(test_engine) as session:
        assert len(list(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))) == 1


def test_competing_proposals_for_one_batch_and_order_are_serialized(test_engine) -> None:
    with Session(test_engine) as session:
        first, version, batch, order, _ = _proposal(session, quantities=(Decimal("6.000"),))
        first_id = first.id
        second = AllocationProposal()
        session.add(second)
        session.flush()
        second_version = AllocationProposalVersion(proposal_id=second.id, version_number=1)
        session.add(second_version)
        session.flush()
        session.add(AllocationLine(
            proposal_version_id=second_version.id, batch_id=batch.id, order_id=order.id,
            proposed_quantity_kg=Decimal("6.000"),
        ))
        session.commit()
        second_id = second.id
    results: list[object] = []
    errors: list[BaseException] = []
    start = threading.Event()

    def submit(proposal_id) -> None:
        start.wait(timeout=5)
        try:
            results.append(submit_allocation_proposal(_factory(test_engine), proposal_id))
        except BaseException as error:
            errors.append(error)

    first_thread = threading.Thread(target=submit, args=(first_id,))
    second_thread = threading.Thread(target=submit, args=(second_id,))
    first_thread.start(); second_thread.start(); start.set()
    first_thread.join(timeout=5); second_thread.join(timeout=5)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], InsufficientBatchCapacityError)


def test_line_change_before_lock_is_detected(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, lines = _proposal(session)
        proposal_id, line_id = proposal.id, lines[0].id
    writer = Session(test_engine)
    line_lock_attempt = threading.Event()
    done = threading.Event()
    errors: list[BaseException] = []

    def observe_line_lock(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FROM allocation_lines" in statement and "FOR UPDATE" in statement:
            line_lock_attempt.set()

    def submit() -> None:
        try:
            submit_allocation_proposal(_factory(test_engine), proposal_id)
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    event.listen(test_engine, "before_cursor_execute", observe_line_lock)
    thread = threading.Thread(target=submit)
    try:
        writer.execute(text("UPDATE allocation_lines SET proposed_quantity_kg = 2.000 WHERE id = :id"), {"id": line_id})
        thread.start()
        assert line_lock_attempt.wait(timeout=5)
        writer.commit()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ProposalTargetsChangedError)
    finally:
        if writer.in_transaction():
            writer.rollback()
        writer.close()
        event.remove(test_engine, "before_cursor_execute", observe_line_lock)
    assert done.is_set()


def test_zero_line_discovery_rechecks_lines_after_proposal_lock(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, batch, order, lines = _proposal(session, quantities=())
        proposal_id, version_id, batch_id, order_id = proposal.id, version.id, batch.id, order.id
        assert lines == []

    writer = Session(test_engine)
    proposal_lock_attempt = threading.Event()
    errors: list[BaseException] = []

    def observe_proposal_lock(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FROM allocation_proposals" in statement and "FOR UPDATE" in statement:
            proposal_lock_attempt.set()

    def submit() -> None:
        try:
            submit_allocation_proposal(_factory(test_engine), proposal_id)
        except BaseException as error:
            errors.append(error)

    writer.execute(text("SELECT id FROM allocation_proposals WHERE id = :id FOR UPDATE"), {"id": proposal_id})
    writer.add(AllocationLine(
        proposal_version_id=version_id, batch_id=batch_id, order_id=order_id,
        proposed_quantity_kg=Decimal("1.000"),
    ))
    writer.flush()
    event.listen(test_engine, "before_cursor_execute", observe_proposal_lock)
    thread = threading.Thread(target=submit)
    try:
        thread.start()
        assert proposal_lock_attempt.wait(timeout=5)
        writer.commit()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ProposalTargetsChangedError)
    finally:
        if writer.in_transaction():
            writer.rollback()
        writer.close()
        event.remove(test_engine, "before_cursor_execute", observe_proposal_lock)
    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        assert stored is not None and stored.status is AllocationProposalStatus.DRAFT
        assert stored.submitted_at is None and stored.expires_at is None
        assert not tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert not tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_submitted",
        )))

    result = submit_allocation_proposal(_factory(test_engine), proposal_id)
    assert len(result.reservation_ids) == 1


def test_submission_uses_global_lock_order(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
    statements: list[str] = []
    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FOR UPDATE" in statement:
            statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        submit_allocation_proposal(_factory(test_engine), proposal.id)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    names = [next(name for name in ("batches", "orders", "allocation_proposals", "allocation_lines", "allocation_reservations") if name in statement) for statement in statements]
    assert names == ["batches", "orders", "allocation_proposals", "allocation_lines"]


def test_approval_commits_reservations_transitions_proposal_and_preserves_quantities(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("1.111"), Decimal("2.222")))
        proposal_id, batch_id, order_id = proposal.id, batch.id, order.id
        batch_quantity, order_quantity = batch.confirmed_kg, order.quantity_kg
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    result = approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        reservations = list(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        approval_event = session.get(AllocationEvent, result.event_id)
        assert stored is not None and stored.status is AllocationProposalStatus.APPROVED and stored.approved_at == result.effective_at
        assert all(item.status is AllocationReservationStatus.COMMITTED and item.committed_at == result.effective_at for item in reservations)
        assert session.get(Batch, batch_id).confirmed_kg == batch_quantity  # type: ignore[union-attr]
        assert session.get(Order, order_id).quantity_kg == order_quantity  # type: ignore[union-attr]
        assert approval_event is not None and approval_event.event_type == "proposal_approved"


def test_expired_missing_and_already_committed_reservations_reject_approval(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        expired_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), expired_id)
    with Session(test_engine) as session:
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == expired_id))
        assert reservation is not None
        reservation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    with pytest.raises(ExpiredReservationError):
        approve_allocation_proposal(_factory(test_engine), expired_id)

    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        committed_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), committed_id)
    with Session(test_engine) as session:
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == committed_id))
        assert reservation is not None
        reservation.status, reservation.committed_at = AllocationReservationStatus.COMMITTED, datetime.now(timezone.utc)
        session.commit()
    with pytest.raises(InvalidReservationStateError):
        approve_allocation_proposal(_factory(test_engine), committed_id)


def test_concurrent_approval_has_one_result_and_one_event(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    results: list[object] = []
    errors: list[BaseException] = []
    start = threading.Event()

    def approve() -> None:
        start.wait(timeout=5)
        try:
            results.append(approve_allocation_proposal(_factory(test_engine), proposal_id))
        except BaseException as error:
            errors.append(error)

    first, second = threading.Thread(target=approve), threading.Thread(target=approve)
    first.start(); second.start(); start.set()
    first.join(timeout=5); second.join(timeout=5)
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], ProposalNotAwaitingApprovalError)
    with Session(test_engine) as session:
        assert len(list(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_approved")))) == 1


def test_missing_reservation_rejects_approval_without_state_or_event_changes(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        reservation = session.scalar(
            select(AllocationReservation)
            .where(AllocationReservation.proposal_id == proposal_id)
            .order_by(AllocationReservation.id)
        )
        assert reservation is not None
        session.delete(reservation)
        session.commit()
    with pytest.raises(MissingProposalReservationsError):
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.PENDING_APPROVAL
        assert list(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id,
            AllocationEvent.event_type == "proposal_approved",
        ))) == []


def test_approval_event_failure_rolls_back_reservations_and_proposal(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)

    def fail_approval_event(_mapper, _connection, target) -> None:
        if target.event_type == "proposal_approved":
            raise RuntimeError("forced approval event failure")

    event.listen(AllocationEvent, "before_insert", fail_approval_event)
    try:
        with pytest.raises(RuntimeError, match="forced approval event failure"):
            approve_allocation_proposal(_factory(test_engine), proposal_id)
    finally:
        event.remove(AllocationEvent, "before_insert", fail_approval_event)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = list(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert proposal is not None and proposal.status is AllocationProposalStatus.PENDING_APPROVAL
        assert all(reservation.status is AllocationReservationStatus.PENDING for reservation in reservations)
        assert list(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id,
            AllocationEvent.event_type == "proposal_approved",
        ))) == []


@pytest.mark.parametrize("proposal_expiry", [None, datetime.now(timezone.utc) - timedelta(seconds=1)])
def test_missing_or_expired_proposal_expiry_rejects_approval(test_engine, proposal_expiry) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        proposal.expires_at = proposal_expiry
        session.commit()
    with pytest.raises(ExpiredReservationError):
        approve_allocation_proposal(_factory(test_engine), proposal_id)


def test_reservation_expiry_must_match_proposal_expiry(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        assert reservation is not None
        reservation.expires_at = reservation.expires_at + timedelta(seconds=1)  # type: ignore[operator]
        session.commit()
    with pytest.raises(InvalidReservationError, match="expiry"):
        approve_allocation_proposal(_factory(test_engine), proposal_id)


@pytest.mark.parametrize("action,approved,expected_proposal,expected_reservation", [
    ("reject", False, AllocationProposalStatus.REJECTED, AllocationReservationStatus.REJECTED),
    ("cancel", False, AllocationProposalStatus.CANCELLED, AllocationReservationStatus.CANCELLED),
    ("cancel", True, AllocationProposalStatus.CANCELLED, AllocationReservationStatus.CANCELLED),
])
def test_terminal_transition_restores_capacity_and_records_reason(test_engine, action, approved, expected_proposal, expected_reservation) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("1.111"), Decimal("2.222")))
        proposal_id, batch_id, order_id = proposal.id, batch.id, order.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    if approved:
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    operation = reject_allocation_proposal if action == "reject" else cancel_allocation_proposal
    result = operation(_factory(test_engine), proposal_id, reason_code="operator_request", reason="No longer needed")
    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        event_record = session.get(AllocationEvent, result.event_id)
        assert stored is not None and stored.status is expected_proposal
        assert (stored.rejected_at if action == "reject" else stored.cancelled_at) == result.effective_at
        assert stored.terminal_reason_code == "operator_request" and stored.terminal_reason == "No longer needed"
        assert len(reservations) == 2
        assert all(item.status is expected_reservation and item.terminal_at == result.effective_at for item in reservations)
        assert all(item.terminal_reason_code == "operator_request" and item.terminal_reason == "No longer needed" for item in reservations)
        assert all((item.committed_at is not None) == approved for item in reservations)
        assert all(not reservation_consumes_capacity(item, result.effective_at) for item in reservations)
        assert session.get(Batch, batch_id).confirmed_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert session.get(Order, order_id).quantity_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert event_record is not None and event_record.event_type == f"proposal_{'rejected' if action == 'reject' else 'cancelled'}"
    with pytest.raises(ProposalNotTerminableError):
        operation(_factory(test_engine), proposal_id, reason_code="operator_request")


def test_terminal_transition_rejects_invalid_reason_and_missing_reservation(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with pytest.raises(InvalidTerminalReasonError):
        reject_allocation_proposal(_factory(test_engine), proposal_id, reason_code=" ")
    with Session(test_engine) as session:
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        assert reservation is not None
        session.delete(reservation)
        session.commit()
    with pytest.raises(MissingProposalReservationsError):
        cancel_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request")


def test_terminal_event_failure_rolls_back_all_reservations(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)

    def fail_event(_mapper, _connection, target) -> None:
        if target.event_type == "proposal_rejected":
            raise RuntimeError("forced terminal event failure")

    event.listen(AllocationEvent, "before_insert", fail_event)
    try:
        with pytest.raises(RuntimeError, match="forced terminal event failure"):
            reject_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request")
    finally:
        event.remove(AllocationEvent, "before_insert", fail_event)
    with Session(test_engine) as session:
        stored = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert stored is not None and stored.status is AllocationProposalStatus.PENDING_APPROVAL and stored.rejected_at is None
        assert len(reservations) == 2 and all(item.status is AllocationReservationStatus.PENDING for item in reservations)
        assert not list(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_rejected")))


def test_rejection_racing_approval_serializes_and_creates_one_terminal_event(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    start = threading.Event()
    successes: list[object] = []
    failures: list[BaseException] = []

    def run(operation) -> None:
        start.wait(timeout=5)
        try:
            successes.append(operation())
        except BaseException as error:
            failures.append(error)

    threads = (threading.Thread(target=run, args=(lambda: approve_allocation_proposal(_factory(test_engine), proposal_id),)),
               threading.Thread(target=run, args=(lambda: reject_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request"),)))
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], (ProposalNotAwaitingApprovalError, ProposalNotTerminableError))
    with Session(test_engine) as session:
        events = tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type.in_(("proposal_approved", "proposal_rejected")))))
        assert len(events) == 1


@pytest.mark.parametrize("action", ("reject", "cancel"))
def test_terminal_operations_follow_global_lock_order(test_engine, action) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FOR UPDATE" in statement:
            statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        operation = reject_allocation_proposal if action == "reject" else cancel_allocation_proposal
        operation(_factory(test_engine), proposal_id, reason_code="operator_request")
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    categories = ("batches", "orders", "allocation_proposals", "allocation_lines", "allocation_reservations")
    assert [next(category for category in categories if category in statement) for statement in statements] == list(categories)


def test_expiry_is_due_at_boundary_and_repeat_is_harmless(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("1.111"), Decimal("2.222")))
        proposal_id, batch_id, order_id = proposal.id, batch.id, order.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    assert expire_pending_reservations(_factory(test_engine), proposal_id) is None
    with Session(test_engine) as session:
        boundary = session.scalar(select(func.clock_timestamp()))
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        proposal.expires_at = boundary
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        for reservation in reservations:
            reservation.expires_at = boundary
        session.commit()
    result = expire_pending_reservations(_factory(test_engine), proposal_id)
    assert result is not None and result.effective_at >= boundary
    assert expire_pending_reservations(_factory(test_engine), proposal_id) is None
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        events = tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_expired")))
        assert proposal is not None and proposal.status is AllocationProposalStatus.EXPIRED
        assert len(reservations) == 2 and all(item.status is AllocationReservationStatus.EXPIRED and item.terminal_at == result.effective_at for item in reservations)
        assert all(not reservation_consumes_capacity(item, result.effective_at) for item in reservations)
        assert len(events) == 1 and events[0].id == result.event_id
        assert session.get(Batch, batch_id).confirmed_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert session.get(Order, order_id).quantity_kg == Decimal("10.000")  # type: ignore[union-attr]


def test_release_committed_reservations_preserves_snapshots_and_rejects_repetition(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.111"), Decimal("2.222")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with pytest.raises(ProposalNotTerminableError):
        release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request")
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        original_commitments = {item.id: item.committed_at for item in session.scalars(
            select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)
        )}
    result = release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request", reason="Order completed")
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        event_record = session.get(AllocationEvent, result.event_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.RELEASED
        assert proposal.terminal_reason_code == "operator_request" and proposal.terminal_reason == "Order completed"
        assert len(reservations) == 2
        assert all(item.status is AllocationReservationStatus.RELEASED and item.committed_at is not None and item.terminal_at == result.effective_at for item in reservations)
        assert all(item.committed_at == original_commitments[item.id] and item.terminal_reason_code == "operator_request"
                   and item.terminal_reason == "Order completed" for item in reservations)
        assert all(item.reserved_quantity_kg == item.allocation_line.proposed_quantity_kg for item in reservations)
        assert all(not reservation_consumes_capacity(item, result.effective_at) for item in reservations)
        assert event_record is not None and event_record.event_type == "proposal_released"
    with pytest.raises(ProposalNotTerminableError):
        release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request")


def test_expiry_event_failure_rolls_back_all_lifecycle_fields(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        boundary = session.scalar(select(func.clock_timestamp()))
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        proposal.expires_at = boundary
        for reservation in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)):
            reservation.expires_at = boundary
        session.commit()

    def fail_event(_mapper, _connection, target) -> None:
        if target.event_type == "proposal_expired":
            raise RuntimeError("forced lifecycle event failure")

    event.listen(AllocationEvent, "before_insert", fail_event)
    try:
        with pytest.raises(RuntimeError, match="forced lifecycle event failure"):
            expire_pending_reservations(_factory(test_engine), proposal_id)
    finally:
        event.remove(AllocationEvent, "before_insert", fail_event)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert proposal is not None and proposal.status is AllocationProposalStatus.PENDING_APPROVAL
        assert proposal.terminal_reason_code is None and proposal.terminal_reason is None
        assert all(item.status is AllocationReservationStatus.PENDING and item.terminal_at is None
                   and item.terminal_reason_code is None and item.terminal_reason is None for item in reservations)
        assert not tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id,
                                                                  AllocationEvent.event_type == "proposal_expired")))


def test_expiry_does_not_change_approved_proposals(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    assert expire_pending_reservations(_factory(test_engine), proposal_id) is None
    with Session(test_engine) as session:
        assert session.get(AllocationProposal, proposal_id).status is AllocationProposalStatus.APPROVED  # type: ignore[union-attr]


@pytest.mark.parametrize("action", ("expire", "release"))
def test_expiry_and_release_follow_global_lock_order(test_engine, action) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    if action == "release":
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    else:
        with Session(test_engine) as session:
            boundary = session.scalar(select(func.clock_timestamp()))
            proposal = session.get(AllocationProposal, proposal_id)
            assert proposal is not None
            proposal.expires_at = boundary
            for reservation in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)):
                reservation.expires_at = boundary
            session.commit()
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "FOR UPDATE" in statement:
            statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        if action == "expire":
            expire_pending_reservations(_factory(test_engine), proposal_id)
        else:
            release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request")
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    categories = ("batches", "orders", "allocation_proposals", "allocation_lines", "allocation_reservations")
    assert [next(category for category in categories if category in statement) for statement in statements] == list(categories)


def _set_due_expiry(engine, proposal_id) -> datetime:
    with Session(engine) as session:
        boundary = session.scalar(select(func.clock_timestamp()))
        assert boundary is not None
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        proposal.expires_at = boundary
        for reservation in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)):
            reservation.expires_at = boundary
        session.commit()
        return boundary


def test_expiry_exactly_at_deadline(test_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    boundary = _set_due_expiry(test_engine, proposal_id)
    monkeypatch.setattr(reservation_service, "effective_timestamp", lambda _session: boundary)
    result = expire_pending_reservations(_factory(test_engine), proposal_id)
    assert result is not None and result.effective_at == boundary


@pytest.mark.parametrize("defect", ("missing", "wrong_status", "expiry_mismatch", "missing_proposal_expiry", "quantity_mismatch"))
def test_expiry_rejects_inconsistent_reservations(test_engine, defect) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    _set_due_expiry(test_engine, proposal_id)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id).order_by(AllocationReservation.id))
        assert proposal is not None and reservation is not None
        if defect == "missing":
            session.delete(reservation)
        elif defect == "wrong_status":
            reservation.status = AllocationReservationStatus.COMMITTED
            reservation.committed_at = datetime.now(timezone.utc)
        elif defect == "expiry_mismatch":
            reservation.expires_at += timedelta(seconds=1)
        elif defect == "missing_proposal_expiry":
            proposal.expires_at = None
        else:
            reservation.reserved_quantity_kg += Decimal("1.000")
        session.commit()
    expected = MissingProposalReservationsError if defect == "missing" else InvalidReservationStateError if defect == "wrong_status" else InvalidReservationError
    with pytest.raises(expected):
        expire_pending_reservations(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.PENDING_APPROVAL
        assert not tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id,
                                                                  AllocationEvent.event_type == "proposal_expired")))


@pytest.mark.parametrize("defect", ("missing", "wrong_status", "quantity_mismatch"))
def test_release_rejects_inconsistent_reservations(test_engine, defect) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id).order_by(AllocationReservation.id))
        assert reservation is not None
        if defect == "missing":
            session.delete(reservation)
        elif defect == "wrong_status":
            reservation.status = AllocationReservationStatus.RELEASED
            reservation.terminal_at = datetime.now(timezone.utc)
        else:
            reservation.reserved_quantity_kg += Decimal("1.000")
        session.commit()
    expected = MissingProposalReservationsError if defect == "missing" else InvalidReservationStateError if defect == "wrong_status" else InvalidReservationError
    with pytest.raises(expected):
        release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request")
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.APPROVED
        assert not tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id,
                                                                  AllocationEvent.event_type == "proposal_released")))


def test_release_event_failure_rolls_back_all_lifecycle_fields(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, batch, order, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id, batch_id, order_id = proposal.id, batch.id, order.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        original_commitments = {item.id: item.committed_at for item in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))}

    def fail_release(_mapper, _connection, target) -> None:
        if target.event_type == "proposal_released":
            raise RuntimeError("forced release event failure")

    event.listen(AllocationEvent, "before_insert", fail_release)
    try:
        with pytest.raises(RuntimeError, match="forced release event failure"):
            release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request",
                                           reason="Order completed", idempotency_key="failed-release")
    finally:
        event.remove(AllocationEvent, "before_insert", fail_release)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert proposal is not None and proposal.status is AllocationProposalStatus.APPROVED
        assert proposal.terminal_reason_code is None and proposal.terminal_reason is None
        assert all(item.status is AllocationReservationStatus.COMMITTED and item.committed_at == original_commitments[item.id]
                   and item.terminal_at is None and item.terminal_reason_code is None and item.terminal_reason is None
                   for item in reservations)
        assert session.get(Batch, batch_id).confirmed_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert session.get(Order, order_id).quantity_kg == Decimal("10.000")  # type: ignore[union-attr]
        assert not tuple(session.scalars(select(AllocationEvent).where(AllocationEvent.proposal_id == proposal_id,
                                                                  AllocationEvent.event_type == "proposal_released")))
        assert not tuple(session.scalars(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.proposal_id == proposal_id,
            AllocationOperationIdempotency.operation_type == "release",
        )))


@pytest.mark.parametrize("first_action,second_action,approved", (
    ("expire", "expire", False), ("expire", "reject", False), ("expire", "cancel", False),
    ("expire", "approve", False), ("release", "release", True), ("release", "cancel", True),
))
def test_competing_expiry_and_release_operations_serialize(test_engine, first_action, second_action, approved) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    if approved:
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    else:
        _set_due_expiry(test_engine, proposal_id)
    operations = {
        "expire": lambda: expire_pending_reservations(_factory(test_engine), proposal_id),
        "release": lambda: release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request"),
        "reject": lambda: reject_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request"),
        "cancel": lambda: cancel_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request"),
        "approve": lambda: approve_allocation_proposal(_factory(test_engine), proposal_id),
    }
    start = threading.Event()
    successes: list[object] = []
    failures: list[BaseException] = []

    def run(action: str) -> None:
        start.wait(timeout=5)
        try:
            successes.append(operations[action]())
        except BaseException as error:
            failures.append(error)

    threads = (threading.Thread(target=run, args=(first_action,)), threading.Thread(target=run, args=(second_action,)))
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) + len(failures) == 2
    assert all(isinstance(error, (ProposalNotTerminableError, ProposalNotAwaitingApprovalError, ExpiredReservationError))
               for error in failures)
    if first_action == second_action == "expire":
        assert len(successes) == 2 and len(failures) == 0 and sum(result is None for result in successes) == 1
    elif first_action == "expire" and second_action in {"reject", "cancel"}:
        assert (len(successes), len(failures)) in {(2, 0), (1, 1)}
    else:
        assert len(successes) == len(failures) == 1
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        terminal_events = tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id,
            AllocationEvent.event_type.in_(("proposal_expired", "proposal_released", "proposal_rejected", "proposal_cancelled")),
        )))
        assert proposal is not None and len(terminal_events) <= 1
        if proposal.status is AllocationProposalStatus.EXPIRED:
            assert len(terminal_events) == 1 and all(item.status is AllocationReservationStatus.EXPIRED for item in reservations)
        elif proposal.status is AllocationProposalStatus.RELEASED:
            assert len(terminal_events) == 1 and all(item.status is AllocationReservationStatus.RELEASED for item in reservations)
        elif proposal.status is AllocationProposalStatus.REJECTED:
            assert len(terminal_events) == 1 and all(item.status is AllocationReservationStatus.REJECTED for item in reservations)
        elif proposal.status is AllocationProposalStatus.CANCELLED:
            assert len(terminal_events) == 1 and all(item.status is AllocationReservationStatus.CANCELLED for item in reservations)
        else:
            assert proposal.status is AllocationProposalStatus.APPROVED and not terminal_events
            assert all(item.status is AllocationReservationStatus.COMMITTED for item in reservations)


@pytest.mark.parametrize("action", ("approve", "reject", "cancel", "release"))
def test_expiry_leaves_other_lifecycle_states_unchanged(test_engine, action) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    if action in {"approve", "release"}:
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    if action == "reject":
        reject_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request")
    elif action == "cancel":
        cancel_allocation_proposal(_factory(test_engine), proposal_id, reason_code="operator_request")
    elif action == "release":
        release_committed_reservations(_factory(test_engine), proposal_id, reason_code="operator_request")
    with Session(test_engine) as session:
        original = session.get(AllocationProposal, proposal_id).status  # type: ignore[union-attr]
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        assert reservation is not None
        original_reservation = (reservation.status, reservation.terminal_at, reservation.terminal_reason_code)
        original_events = tuple(session.scalars(select(AllocationEvent.id).where(AllocationEvent.proposal_id == proposal_id)))
    assert expire_pending_reservations(_factory(test_engine), proposal_id) is None
    with Session(test_engine) as session:
        assert session.get(AllocationProposal, proposal_id).status is original  # type: ignore[union-attr]
        reservation = session.scalar(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        assert reservation is not None
        assert (reservation.status, reservation.terminal_at, reservation.terminal_reason_code) == original_reservation
        assert tuple(session.scalars(select(AllocationEvent.id).where(AllocationEvent.proposal_id == proposal_id))) == original_events


def test_expiry_uses_one_database_timestamp_after_locks(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    _set_due_expiry(test_engine, proposal_id)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        expire_pending_reservations(_factory(test_engine), proposal_id)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    clocks = [index for index, statement in enumerate(statements) if "clock_timestamp()" in statement]
    locks = [index for index, statement in enumerate(statements) if "FOR UPDATE" in statement]
    assert len(clocks) == 1 and locks and clocks[0] > max(locks)


def test_keyed_release_replays_result_and_detects_different_inputs(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    first = release_committed_reservations(
        _factory(test_engine), proposal_id, reason_code="operator_request",
        reason="Order completed", idempotency_key="release-001",
    )
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        original_proposal_state = (proposal.status, proposal.terminal_reason_code, proposal.terminal_reason, proposal.updated_at)
        original_reservations = {
            item.id: (item.status, item.committed_at, item.terminal_at, item.terminal_reason_code,
                      item.terminal_reason, item.updated_at)
            for item in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        }
    replay = release_committed_reservations(
        _factory(test_engine), proposal_id, reason_code=" operator_request ",
        reason=" Order completed ", idempotency_key="release-001",
    )
    assert replay == first
    with pytest.raises(IdempotencyConflictError):
        release_committed_reservations(
            _factory(test_engine), proposal_id, reason_code="different_reason", idempotency_key="release-001",
        )
    with pytest.raises(ProposalNotTerminableError):
        release_committed_reservations(
            _factory(test_engine), proposal_id, reason_code="operator_request",
            reason="Order completed", idempotency_key="release-002",
        )
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        assert proposal is not None
        assert (proposal.status, proposal.terminal_reason_code, proposal.terminal_reason, proposal.updated_at) == original_proposal_state
        assert {
            item.id: (item.status, item.committed_at, item.terminal_at, item.terminal_reason_code,
                      item.terminal_reason, item.updated_at)
            for item in session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id))
        } == original_reservations
        events = tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_released",
        )))
        keys = tuple(session.scalars(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.operation_type == "release",
        )))
        assert len(events) == len(keys) == 1
        assert keys[0].result_event_id == first.event_id


def test_release_key_is_exact_and_rejects_different_proposal(test_engine) -> None:
    with Session(test_engine) as session:
        first, _, _, _, _ = _proposal(session)
        second, _, _, _, _ = _proposal(session)
        first_id, second_id = first.id, second.id
    for proposal_id in (first_id, second_id):
        submit_allocation_proposal(_factory(test_engine), proposal_id)
        approve_allocation_proposal(_factory(test_engine), proposal_id)
    first_result = release_committed_reservations(
        _factory(test_engine), first_id, reason_code="operator_request", idempotency_key=" release-key ",
    )
    assert release_committed_reservations(
        _factory(test_engine), first_id, reason_code="operator_request", idempotency_key=" release-key ",
    ) == first_result
    with pytest.raises(ProposalNotTerminableError):
        release_committed_reservations(
            _factory(test_engine), first_id, reason_code="operator_request", idempotency_key="release-key",
        )
    with pytest.raises(IdempotencyConflictError):
        release_committed_reservations(
            _factory(test_engine), second_id, reason_code="operator_request", idempotency_key=" release-key ",
        )
    with Session(test_engine) as session:
        second = session.get(AllocationProposal, second_id)
        assert second is not None and second.status is AllocationProposalStatus.APPROVED
        assert not tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == second_id, AllocationEvent.event_type == "proposal_released",
        )))


def test_corrupt_stored_release_result_does_not_replay(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    release_committed_reservations(
        _factory(test_engine), proposal_id, reason_code="operator_request", idempotency_key="release-corrupt",
    )
    with Session(test_engine) as session:
        operation = session.scalar(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.idempotency_key == "release-corrupt",
        ))
        assert operation is not None and operation.result_json is not None
        operation.result_json = {**operation.result_json, "proposal_id": str(uuid4())}
        session.commit()
    with pytest.raises(IdempotencyConflictError, match="invalid stored result"):
        release_committed_reservations(
            _factory(test_engine), proposal_id, reason_code="operator_request", idempotency_key="release-corrupt",
        )


def test_concurrent_matching_key_release_replays_one_result(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    start = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def release() -> None:
        start.wait(timeout=5)
        try:
            results.append(release_committed_reservations(
                _factory(test_engine), proposal_id, reason_code="operator_request", idempotency_key="release-concurrent",
            ))
        except BaseException as error:
            errors.append(error)

    threads = (threading.Thread(target=release), threading.Thread(target=release))
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors and len(results) == 2 and results[0] == results[1]
    with Session(test_engine) as session:
        events = tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_released",
        )))
        assert len(events) == 1


def test_concurrent_different_fingerprints_for_one_release_key_conflict(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session)
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    start = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def release(reason: str) -> None:
        start.wait(timeout=5)
        try:
            results.append(release_committed_reservations(
                _factory(test_engine), proposal_id, reason_code="operator_request",
                reason=reason, idempotency_key="release-conflict",
            ))
        except BaseException as error:
            errors.append(error)

    threads = (threading.Thread(target=release, args=("First reason",)),
               threading.Thread(target=release, args=("Second reason",)))
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == len(errors) == 1 and isinstance(errors[0], IdempotencyConflictError)
    with Session(test_engine) as session:
        events = tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_released",
        )))
        operations = tuple(session.scalars(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.idempotency_key == "release-conflict",
        )))
        assert len(events) == len(operations) == 1
        assert operations[0].result_event_id == events[0].id


def test_idempotency_insert_failure_rolls_back_release_and_allows_retry(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, _, _, _, _ = _proposal(session, quantities=(Decimal("1.000"), Decimal("2.000")))
        proposal_id = proposal.id
    submit_allocation_proposal(_factory(test_engine), proposal_id)
    approve_allocation_proposal(_factory(test_engine), proposal_id)
    with Session(test_engine) as session:
        commitments = {item.id: item.committed_at for item in session.scalars(
            select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)
        )}

    def fail_idempotency(_mapper, _connection, target) -> None:
        if target.operation_type == "release":
            raise RuntimeError("forced idempotency insert failure")

    event.listen(AllocationOperationIdempotency, "before_insert", fail_idempotency)
    try:
        with pytest.raises(RuntimeError, match="forced idempotency insert failure"):
            release_committed_reservations(
                _factory(test_engine), proposal_id, reason_code="operator_request",
                idempotency_key="release-retry",
            )
    finally:
        event.remove(AllocationOperationIdempotency, "before_insert", fail_idempotency)
    with Session(test_engine) as session:
        proposal = session.get(AllocationProposal, proposal_id)
        reservations = tuple(session.scalars(select(AllocationReservation).where(AllocationReservation.proposal_id == proposal_id)))
        assert proposal is not None and proposal.status is AllocationProposalStatus.APPROVED
        assert proposal.terminal_reason_code is None and proposal.terminal_reason is None
        assert all(item.status is AllocationReservationStatus.COMMITTED and item.committed_at == commitments[item.id]
                   and item.terminal_at is None and item.terminal_reason_code is None and item.terminal_reason is None
                   for item in reservations)
        assert not tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_released",
        )))
        assert not tuple(session.scalars(select(AllocationOperationIdempotency).where(
            AllocationOperationIdempotency.idempotency_key == "release-retry",
        )))
    result = release_committed_reservations(
        _factory(test_engine), proposal_id, reason_code="operator_request", idempotency_key="release-retry",
    )
    with Session(test_engine) as session:
        events = tuple(session.scalars(select(AllocationEvent).where(
            AllocationEvent.proposal_id == proposal_id, AllocationEvent.event_type == "proposal_released",
        )))
        assert len(events) == 1 and events[0].id == result.event_id
