from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationOperationIdempotency,
    AllocationOperationIdempotencyState,
    AllocationProposal,
    AllocationProposalVersion,
    AllocationQualityGrade,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    Buyer,
    Grower,
    Order,
)


def _batch_and_order(session: Session) -> tuple[Batch, Order]:
    grower = Grower(name="Allocation grower", phone="9999999999", location="Pune")
    buyer = Buyer(name="Allocation buyer", location="Pune", contact="buyer@example.invalid")
    session.add_all([grower, buyer])
    session.flush()
    batch = Batch(
        grower_id=grower.id,
        species="oyster",
        expected_kg=Decimal("10.000"),
        confirmed_kg=Decimal("8.000"),
        harvest_date=date(2026, 11, 10),
        quality_grade="Grade A",
    )
    order = Order(
        buyer_id=buyer.id,
        species="oyster",
        quantity_kg=Decimal("6.000"),
        price_limit=Decimal("100.00"),
        delivery_date=date(2026, 11, 11),
        required_quality_grade="Export Quality",
    )
    session.add_all([batch, order])
    session.flush()
    return batch, order


def test_allocation_models_preserve_legacy_grades_and_relationships(test_engine) -> None:
    with Session(test_engine) as session:
        batch, order = _batch_and_order(session)
        assert batch.allocation_quality_grade is None
        assert order.allocation_required_quality_grade is None

        batch.allocation_quality_grade = AllocationQualityGrade.PREMIUM
        order.allocation_required_quality_grade = AllocationQualityGrade.STANDARD
        proposal = AllocationProposal(ruleset_version="phase-3.1")
        session.add(proposal)
        session.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        session.add(version)
        session.flush()
        line = AllocationLine(
            proposal_version_id=version.id,
            batch_id=batch.id,
            order_id=order.id,
            proposed_quantity_kg=Decimal("1.234"),
        )
        event = AllocationEvent(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            event_type="proposal_created",
            details={"source": "model-test"},
        )
        session.add_all([line, event])
        session.commit()

        stored = session.get(AllocationLine, line.id)
        assert stored is not None
        assert stored.proposed_quantity_kg == Decimal("1.234")
        assert stored.batch.quality_grade == "Grade A"
        assert stored.order.required_quality_grade == "Export Quality"
        assert stored.batch.allocation_quality_grade is AllocationQualityGrade.PREMIUM
        assert stored.order.allocation_required_quality_grade is AllocationQualityGrade.STANDARD
        assert stored.proposal_version.proposal.id == proposal.id
        assert len(stored.batch.allocation_lines) == 1
        assert len(stored.order.allocation_lines) == 1
        assert event.details == {"source": "model-test"}


@pytest.mark.parametrize("quantity", [Decimal("0.000"), Decimal("-0.001")])
def test_allocation_line_quantity_must_be_positive(test_engine, quantity: Decimal) -> None:
    with Session(test_engine) as session:
        batch, order = _batch_and_order(session)
        proposal = AllocationProposal()
        session.add(proposal)
        session.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        session.add(version)
        session.flush()
        session.add(
            AllocationLine(
                proposal_version_id=version.id,
                batch_id=batch.id,
                order_id=order.id,
                proposed_quantity_kg=quantity,
            )
        )
        with pytest.raises((DataError, IntegrityError)):
            session.commit()


def test_allocation_foreign_keys_and_version_number_are_enforced(test_engine) -> None:
    with Session(test_engine) as session:
        proposal = AllocationProposal()
        session.add(proposal)
        session.flush()
        session.add_all(
            [
                AllocationProposalVersion(proposal_id=proposal.id, version_number=1),
                AllocationProposalVersion(proposal_id=proposal.id, version_number=1),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(test_engine) as session:
        session.add(AllocationProposalVersion(proposal_id=uuid4(), version_number=1))
        with pytest.raises(IntegrityError):
            session.commit()


def test_controlled_quality_grades_reject_unapproved_values(test_engine) -> None:
    with Session(test_engine) as session:
        batch, _ = _batch_and_order(session)
        batch.allocation_quality_grade = "UNKNOWN"
        with pytest.raises((DataError, IntegrityError)):
            session.commit()


def test_allocation_event_version_must_belong_to_its_proposal(test_engine) -> None:
    with Session(test_engine) as session:
        first = AllocationProposal()
        second = AllocationProposal()
        session.add_all([first, second])
        session.flush()
        second_version = AllocationProposalVersion(proposal_id=second.id, version_number=1)
        session.add(second_version)
        session.flush()
        session.add(
            AllocationEvent(
                proposal_id=first.id,
                proposal_version_id=second_version.id,
                event_type="invalid_cross_proposal_event",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def _proposal_version_line(session: Session) -> tuple[AllocationProposal, AllocationProposalVersion, AllocationLine]:
    batch, order = _batch_and_order(session)
    proposal = AllocationProposal()
    session.add(proposal)
    session.flush()
    version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
    session.add(version)
    session.flush()
    line = AllocationLine(
        proposal_version_id=version.id,
        batch_id=batch.id,
        order_id=order.id,
        proposed_quantity_kg=Decimal("1.234"),
    )
    session.add(line)
    session.flush()
    return proposal, version, line


def test_reservation_relationships_lifecycle_metadata_and_nullable_event_link(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        reservation = AllocationReservation(
            allocation_line_id=line.id,
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reserved_quantity_kg=Decimal("1.234"),
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        unlinked_event = AllocationEvent(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            event_type="proposal_generated",
        )
        linked_event = AllocationEvent(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reservation=reservation,
            event_type="reservation_created",
        )
        session.add_all([reservation, unlinked_event, linked_event])
        session.commit()

        assert proposal.submitted_at is None
        assert proposal.approved_at is None
        assert proposal.rejected_at is None
        assert proposal.cancelled_at is None
        assert proposal.expires_at is None
        assert proposal.terminal_reason_code is None
        assert proposal.terminal_reason is None
        assert line.reservation is not None and line.reservation.id == reservation.id
        assert reservation.proposal_version.proposal_id == proposal.id
        assert linked_event.reservation_id == reservation.id
        assert unlinked_event.reservation_id is None
        assert reservation.events == [linked_event]


@pytest.mark.parametrize("quantity", [Decimal("0.000"), Decimal("-0.001")])
def test_reservation_quantity_must_be_positive(test_engine, quantity: Decimal) -> None:
    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        session.add(
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
                reserved_quantity_kg=quantity,
                status=AllocationReservationStatus.PENDING,
                expires_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises((DataError, IntegrityError)):
            session.commit()


def test_reservation_numeric_precision_and_unique_line_are_enforced(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        reservation = AllocationReservation(
            allocation_line_id=line.id,
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reserved_quantity_kg=line.proposed_quantity_kg,
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime.now(timezone.utc),
        )
        session.add(reservation)
        session.commit()
        assert session.get(AllocationReservation, reservation.id).reserved_quantity_kg == Decimal("1.234")

    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        first = AllocationReservation(
            allocation_line_id=line.id,
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reserved_quantity_kg=line.proposed_quantity_kg,
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime.now(timezone.utc),
        )
        second = AllocationReservation(
            allocation_line_id=line.id,
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            reserved_quantity_kg=line.proposed_quantity_kg,
            status=AllocationReservationStatus.PENDING,
            expires_at=datetime.now(timezone.utc),
        )
        session.add_all([first, second])
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        session.add(
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
                reserved_quantity_kg=Decimal("1000000000.000"),
                status=AllocationReservationStatus.PENDING,
                expires_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises((DataError, IntegrityError)):
            session.commit()


def test_reservation_status_and_cross_proposal_constraints_are_enforced(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        session.add(
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
                reserved_quantity_kg=Decimal("1.000"),
                status="UNKNOWN",
                expires_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises((DataError, IntegrityError)):
            session.commit()

    with Session(test_engine) as session:
        first, _, line = _proposal_version_line(session)
        second = AllocationProposal()
        session.add(second)
        session.flush()
        second_version = AllocationProposalVersion(proposal_id=second.id, version_number=1)
        session.add(second_version)
        session.flush()
        session.add(
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=second.id,
                proposal_version_id=second_version.id,
            reserved_quantity_kg=line.proposed_quantity_kg,
                status=AllocationReservationStatus.PENDING,
                expires_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(test_engine) as session:
        proposal, version, line = _proposal_version_line(session)
        session.add(
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
            reserved_quantity_kg=line.proposed_quantity_kg,
                status=AllocationReservationStatus.PENDING,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_idempotency_constraints_and_result_event_relationship(test_engine) -> None:
    with Session(test_engine) as session:
        proposal, version, _ = _proposal_version_line(session)
        event = AllocationEvent(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            event_type="proposal_submitted",
        )
        operation = AllocationOperationIdempotency(
            operation_type="submit",
            idempotency_key="submit-001",
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            request_fingerprint="a" * 64,
            state=AllocationOperationIdempotencyState.COMPLETED,
            result_event=event,
            result_json={"result": "stored"},
        )
        session.add_all([event, operation])
        session.commit()
        assert operation.result_event_id == event.id
        assert operation.proposal_version is not None and operation.proposal_version.id == version.id

    with Session(test_engine) as session:
        proposal, _, _ = _proposal_version_line(session)
        session.add_all([
            AllocationOperationIdempotency(
                operation_type="submit", idempotency_key="same", proposal_id=proposal.id,
                request_fingerprint="a" * 64, state=AllocationOperationIdempotencyState.COMPLETED,
            ),
            AllocationOperationIdempotency(
                operation_type="submit", idempotency_key="same", proposal_id=proposal.id,
                request_fingerprint="b" * 64, state=AllocationOperationIdempotencyState.COMPLETED,
            ),
        ])
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(test_engine) as session:
        proposal, _, _ = _proposal_version_line(session)
        session.add(
            AllocationOperationIdempotency(
                operation_type="submit", idempotency_key="invalid-state", proposal_id=proposal.id,
                request_fingerprint="a" * 64, state="UNKNOWN",
            )
        )
        with pytest.raises((DataError, IntegrityError)):
            session.commit()
