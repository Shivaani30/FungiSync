from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.allocation_preview import (
    AllocationPreviewCapacityError,
    calculate_reservation_aware_preview,
)
from app.models import (
    AllocationLine,
    AllocationQualityGrade,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    BatchStatus,
    Order,
    OrderStatus,
)
from app.schemas import AllocationPreviewOut


def _order(quantity: str = "10") -> Order:
    return Order(
        id=uuid4(),
        buyer_id=uuid4(),
        species="oyster",
        quantity_kg=Decimal(quantity),
        price_limit=Decimal("100.00"),
        delivery_date=datetime(2026, 11, 30, tzinfo=timezone.utc).date(),
        required_quality_grade="premium",
        allocation_required_quality_grade=AllocationQualityGrade.PREMIUM,
        status=OrderStatus.PENDING,
    )


def _batch(quantity: str = "8", *, identifier=None) -> Batch:
    return Batch(
        id=identifier or uuid4(),
        grower_id=uuid4(),
        species="oyster",
        expected_kg=Decimal(quantity),
        confirmed_kg=Decimal(quantity),
        harvest_date=datetime(2026, 11, 20, tzinfo=timezone.utc).date(),
        asking_price_per_kg=Decimal("90.00"),
        quality_grade="premium",
        allocation_quality_grade=AllocationQualityGrade.PREMIUM,
        best_before_date=datetime(2026, 12, 5, tzinfo=timezone.utc).date(),
        status=BatchStatus.CONFIRMED,
    )


def _reservation(
    batch: Batch,
    order: Order,
    quantity: str,
    status: AllocationReservationStatus,
    *,
    effective_at: datetime,
    expires_at: datetime | None = None,
) -> AllocationReservation:
    line = AllocationLine(
        id=uuid4(),
        proposal_version_id=uuid4(),
        batch_id=batch.id,
        order_id=order.id,
        proposed_quantity_kg=Decimal(quantity),
    )
    return AllocationReservation(
        id=uuid4(),
        allocation_line_id=line.id,
        proposal_id=uuid4(),
        proposal_version_id=line.proposal_version_id,
        reserved_quantity_kg=Decimal(quantity),
        status=status,
        expires_at=expires_at,
        allocation_line=line,
    )


def test_preview_deducts_active_pending_and_committed_reservations_without_mutating_inputs():
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    order = _order("10")
    first = _batch("8", identifier=UUID(int=1))
    second = _batch("5", identifier=UUID(int=2))
    original_order_quantity = order.quantity_kg
    original_batch_quantities = (first.confirmed_kg, second.confirmed_kg)
    reservations = [
        _reservation(
            first,
            order,
            "2",
            AllocationReservationStatus.PENDING,
            effective_at=effective_at,
            expires_at=effective_at + timedelta(hours=1),
        ),
        _reservation(
            second,
            _order("10"),
            "1",
            AllocationReservationStatus.COMMITTED,
            effective_at=effective_at,
        ),
    ]

    result = calculate_reservation_aware_preview(
        order, [first, second], reservations, effective_at=effective_at
    )

    assert result.total_requested_kg == Decimal("10")
    assert result.active_reserved_order_kg == Decimal("2")
    assert result.remaining_order_kg == Decimal("8")
    assert result.total_allocated_kg == Decimal("8")
    assert result.total_unfulfilled_kg == Decimal("0")
    assert [line.proposed_quantity_kg for line in result.allocation_lines] == [
        Decimal("6"), Decimal("2")
    ]
    assert result.allocation_lines[0].remaining_before_preview_kg == Decimal("6")
    assert result.allocation_lines[1].remaining_before_preview_kg == Decimal("4")
    assert result.is_advisory is True
    assert result.is_synthetic is False
    assert result.persisted is False
    assert result.reservation_committed is False
    assert result.buyer_commitment_created is False
    assert result.reservation_accounting_applied is True
    assert order.quantity_kg == original_order_quantity
    assert (first.confirmed_kg, second.confirmed_kg) == original_batch_quantities


@pytest.mark.parametrize(
    ("status", "expires_at", "expected_reserved"),
    [
        (AllocationReservationStatus.REJECTED, None, Decimal("0")),
        (AllocationReservationStatus.CANCELLED, None, Decimal("0")),
        (AllocationReservationStatus.EXPIRED, None, Decimal("0")),
        (AllocationReservationStatus.RELEASED, None, Decimal("0")),
        (
            AllocationReservationStatus.PENDING,
            datetime(2026, 11, 24, tzinfo=timezone.utc),
            Decimal("0"),
        ),
        (
            AllocationReservationStatus.PENDING,
            datetime(2026, 11, 26, tzinfo=timezone.utc),
            Decimal("2"),
        ),
        (AllocationReservationStatus.COMMITTED, None, Decimal("2")),
    ],
)
def test_preview_uses_existing_terminal_and_expiry_accounting(
    status, expires_at, expected_reserved
):
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    order = _order("5")
    batch = _batch("5")
    reservation = _reservation(
        batch,
        order,
        "2",
        status,
        effective_at=effective_at,
        expires_at=expires_at,
    )

    result = calculate_reservation_aware_preview(
        order, [batch], [reservation], effective_at=effective_at
    )

    assert result.active_reserved_order_kg == expected_reserved
    assert result.remaining_order_kg == Decimal("5") - expected_reserved
    assert result.allocation_lines[0].remaining_before_preview_kg == (
        Decimal("5") - expected_reserved
    )


def test_preview_reports_unfulfilled_quantity_and_ineligible_batch_exclusion():
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    order = _order("10")
    eligible = _batch("3")
    ineligible = _batch("8")
    ineligible.status = BatchStatus.EXPECTED

    result = calculate_reservation_aware_preview(
        order, [eligible, ineligible], [], effective_at=effective_at
    )

    assert result.total_allocated_kg == Decimal("3")
    assert result.total_unfulfilled_kg == Decimal("7")
    assert result.excluded_batches[0].batch_id == ineligible.id
    assert result.excluded_batches[0].reasons[0].code == "BATCH_STATUS_INELIGIBLE"
    assert result.is_reconciled is True


def test_preview_rejects_negative_remaining_capacity():
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    order = _order("5")
    batch = _batch("1")
    reservation = _reservation(
        batch,
        _order("5"),
        "2",
        AllocationReservationStatus.COMMITTED,
        effective_at=effective_at,
    )

    with pytest.raises(AllocationPreviewCapacityError):
        calculate_reservation_aware_preview(
            order, [batch], [reservation], effective_at=effective_at
        )


def test_preview_rejects_reservations_exceeding_order_quantity():
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    order = _order("1")
    batch = _batch("5")
    reservation = _reservation(
        batch,
        order,
        "2",
        AllocationReservationStatus.COMMITTED,
        effective_at=effective_at,
    )

    with pytest.raises(AllocationPreviewCapacityError, match="order quantity"):
        calculate_reservation_aware_preview(
            order, [batch], [reservation], effective_at=effective_at
        )


def test_preview_response_schema_preserves_decimal_strings_and_safety_flags():
    effective_at = datetime(2026, 11, 25, tzinfo=timezone.utc)
    result = calculate_reservation_aware_preview(
        _order("3.125"), [_batch("2.000")], [], effective_at=effective_at
    )

    payload = AllocationPreviewOut.model_validate(asdict(result)).model_dump(mode="json")

    assert payload["total_requested_kg"] == "3.125"
    assert payload["total_allocated_kg"] == "2.000"
    assert payload["total_unfulfilled_kg"] == "1.125"
    assert payload["allocation_lines"][0]["proposed_quantity_kg"] == "2.000"
    assert payload["is_synthetic"] is False
    assert payload["persisted"] is False
    assert payload["reservation_committed"] is False
    assert payload["buyer_commitment_created"] is False
