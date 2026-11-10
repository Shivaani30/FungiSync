from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationProposal,
    AllocationProposalVersion,
    AllocationQualityGrade,
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
