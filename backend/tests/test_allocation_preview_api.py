"""Database-backed route tests; admin sessions only seed setup records."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AllocationLine,
    AllocationEvent,
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


def _seed_preview_records(test_admin_engine):
    now = datetime.now(timezone.utc)
    with Session(test_admin_engine) as session:
        grower = Grower(name="Preview grower", phone="7000000000", location="Samba")
        buyer = Buyer(name="Preview buyer", location="Jammu", contact="demo@example.invalid")
        session.add_all([grower, buyer])
        session.flush()

        order = Order(
            buyer_id=buyer.id,
            species="oyster",
            quantity_kg=Decimal("10.000"),
            price_limit=Decimal("100.00"),
            delivery_date=date(2026, 12, 1),
            required_quality_grade="premium",
            allocation_required_quality_grade=AllocationQualityGrade.PREMIUM,
            status=OrderStatus.PENDING,
        )
        first_batch = Batch(
            grower_id=grower.id,
            species="oyster",
            expected_kg=Decimal("8.000"),
            confirmed_kg=Decimal("8.000"),
            harvest_date=date(2026, 11, 20),
            asking_price_per_kg=Decimal("90.00"),
            quality_grade="premium",
            allocation_quality_grade=AllocationQualityGrade.PREMIUM,
            best_before_date=date(2026, 12, 5),
            status=BatchStatus.CONFIRMED,
        )
        second_batch = Batch(
            grower_id=grower.id,
            species="oyster",
            expected_kg=Decimal("5.000"),
            confirmed_kg=Decimal("5.000"),
            harvest_date=date(2026, 11, 20),
            asking_price_per_kg=Decimal("90.00"),
            quality_grade="premium",
            allocation_quality_grade=AllocationQualityGrade.PREMIUM,
            best_before_date=date(2026, 12, 5),
            status=BatchStatus.CONFIRMED,
        )
        session.add_all([order, first_batch, second_batch])
        session.flush()

        proposal = AllocationProposal(status=AllocationProposalStatus.DRAFT)
        session.add(proposal)
        session.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        session.add(version)
        session.flush()

        pending_line = AllocationLine(
            proposal_version_id=version.id,
            batch_id=first_batch.id,
            order_id=order.id,
            proposed_quantity_kg=Decimal("2.000"),
        )
        committed_line = AllocationLine(
            proposal_version_id=version.id,
            batch_id=second_batch.id,
            order_id=order.id,
            proposed_quantity_kg=Decimal("1.000"),
        )
        session.add_all([pending_line, committed_line])
        session.flush()
        session.add_all([
            AllocationReservation(
                allocation_line_id=pending_line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
                reserved_quantity_kg=Decimal("2.000"),
                status=AllocationReservationStatus.PENDING,
                expires_at=now + timedelta(hours=1),
            ),
            AllocationReservation(
                allocation_line_id=committed_line.id,
                proposal_id=proposal.id,
                proposal_version_id=version.id,
                reserved_quantity_kg=Decimal("1.000"),
                status=AllocationReservationStatus.COMMITTED,
                committed_at=now,
            ),
        ])
        session.commit()
        return order.id


def _allocation_state(test_admin_engine):
    with Session(test_admin_engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                AllocationProposal,
                AllocationProposalVersion,
                AllocationLine,
                AllocationReservation,
                AllocationEvent,
            )
        )


def test_read_only_preview_accounts_for_active_reservations_and_is_deterministic(
    client, test_admin_engine
):
    order_id = _seed_preview_records(test_admin_engine)
    before = _allocation_state(test_admin_engine)

    first = client.get(f"/allocation-preview/orders/{order_id}")
    second = client.get(f"/allocation-preview/orders/{order_id}")

    assert first.status_code == 200
    payload = first.json()
    repeated = second.json()
    assert second.status_code == 200
    assert payload["order_id"] == str(order_id)
    assert payload["is_advisory"] is True
    assert payload["is_synthetic"] is False
    assert payload["persisted"] is False
    assert payload["reservation_committed"] is False
    assert payload["buyer_commitment_created"] is False
    assert payload["reservation_accounting_applied"] is True
    assert payload["quantity_basis"] == (
        "confirmed_batch_and_order_quantities_less_active_reservations"
    )
    assert payload["total_requested_kg"] == "10.000"
    assert payload["active_reserved_order_kg"] == "3.000"
    assert payload["remaining_order_kg"] == "7.000"
    assert payload["total_allocated_kg"] == "7.000"
    assert payload["total_unfulfilled_kg"] == "0.000"
    assert payload["is_reconciled"] is True
    assert payload["allocation_lines"] == repeated["allocation_lines"]
    assert payload["total_allocated_kg"] == repeated["total_allocated_kg"]
    assert _allocation_state(test_admin_engine) == before


def test_missing_order_returns_existing_not_found_convention(client):
    response = client.get(f"/allocation-preview/orders/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


def test_malformed_order_id_is_rejected_by_path_validation(client):
    response = client.get("/allocation-preview/orders/not-a-uuid")
    assert response.status_code == 422
