from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import AllocationLine, AllocationReservation, AllocationReservationStatus, Batch, Order
from app.quantity_accounting import active_reserved_quantity, batch_available_kg, order_remaining_kg, reservation_consumes_capacity


NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def _batch(quantity: Decimal = Decimal("10.000")) -> Batch:
    return Batch(id=uuid4(), grower_id=uuid4(), species="oyster", expected_kg=quantity, confirmed_kg=quantity, harvest_date=NOW.date())


def _order(quantity: Decimal = Decimal("10.000")) -> Order:
    return Order(id=uuid4(), buyer_id=uuid4(), species="oyster", quantity_kg=quantity, price_limit=Decimal("100.00"), delivery_date=NOW.date())


def _reservation(batch: Batch, order: Order, quantity: Decimal, status: AllocationReservationStatus, expires_at: datetime | None = None) -> AllocationReservation:
    line = AllocationLine(id=uuid4(), proposal_version_id=uuid4(), batch_id=batch.id, order_id=order.id, proposed_quantity_kg=quantity)
    return AllocationReservation(id=uuid4(), allocation_line_id=line.id, proposal_id=uuid4(), proposal_version_id=line.proposal_version_id, reserved_quantity_kg=quantity, status=status, expires_at=expires_at, allocation_line=line)


def test_committed_pending_and_expiry_boundaries_consume_correctly() -> None:
    batch, order = _batch(), _order()
    committed = _reservation(batch, order, Decimal("1.111"), AllocationReservationStatus.COMMITTED)
    future = _reservation(batch, order, Decimal("2.222"), AllocationReservationStatus.PENDING, NOW + timedelta(seconds=1))
    exact = _reservation(batch, order, Decimal("3.333"), AllocationReservationStatus.PENDING, NOW)
    past = _reservation(batch, order, Decimal("4.444"), AllocationReservationStatus.PENDING, NOW - timedelta(seconds=1))
    assert [reservation_consumes_capacity(item, NOW) for item in (committed, future, exact, past)] == [True, True, False, False]
    assert active_reserved_quantity((committed, future, exact, past), effective_at=NOW) == Decimal("3.333")


@pytest.mark.parametrize("status", [AllocationReservationStatus.REJECTED, AllocationReservationStatus.CANCELLED, AllocationReservationStatus.EXPIRED, AllocationReservationStatus.RELEASED])
def test_terminal_reservations_consume_nothing(status) -> None:
    batch, order = _batch(), _order()
    reservation = _reservation(batch, order, Decimal("1.000"), status)
    assert not reservation_consumes_capacity(reservation, NOW)
    assert active_reserved_quantity((reservation,), effective_at=NOW) == Decimal("0")


def test_batch_and_order_accounting_filter_through_allocation_line() -> None:
    first_batch, second_batch, first_order, second_order = _batch(Decimal("9.999")), _batch(), _order(Decimal("8.888")), _order()
    first = _reservation(first_batch, first_order, Decimal("1.111"), AllocationReservationStatus.COMMITTED)
    second = _reservation(first_batch, second_order, Decimal("2.222"), AllocationReservationStatus.PENDING, NOW + timedelta(minutes=1))
    third = _reservation(second_batch, first_order, Decimal("3.333"), AllocationReservationStatus.COMMITTED)
    reservations = (first, second, third)
    assert batch_available_kg(first_batch, reservations, effective_at=NOW) == Decimal("6.666")
    assert order_remaining_kg(first_order, reservations, effective_at=NOW) == Decimal("4.444")
    assert batch_available_kg(second_batch, (), effective_at=NOW) == Decimal("10.000")


def test_invalid_inputs_are_rejected_safely() -> None:
    batch, order = _batch(), _order()
    reservation = _reservation(batch, order, Decimal("1.000"), AllocationReservationStatus.PENDING, NOW + timedelta(minutes=1))
    with pytest.raises(ValueError):
        batch_available_kg(batch, (reservation,), effective_at=NOW.replace(tzinfo=None))
    reservation.reserved_quantity_kg = Decimal("NaN")
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)
    batch.confirmed_kg = None
    with pytest.raises(ValueError):
        batch_available_kg(batch, (), effective_at=NOW)


def test_invalid_reservation_identity_status_and_pending_expiry_are_rejected() -> None:
    batch, order = _batch(), _order()
    reservation = _reservation(batch, order, Decimal("1.000"), AllocationReservationStatus.PENDING, NOW + timedelta(minutes=1))
    with pytest.raises(ValueError):
        active_reserved_quantity((object(),), effective_at=NOW)
    reservation.allocation_line = None
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)
    reservation = _reservation(batch, order, Decimal("1.000"), AllocationReservationStatus.PENDING, NOW + timedelta(minutes=1))
    reservation.status = "UNKNOWN"
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)
    reservation.status = AllocationReservationStatus.PENDING
    reservation.expires_at = None
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)
    reservation.expires_at = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)


@pytest.mark.parametrize("quantity", [Decimal("-0.001"), Decimal("NaN"), Decimal("Infinity"), 1.0])
def test_invalid_active_reservation_quantities_are_rejected(quantity) -> None:
    batch, order = _batch(), _order()
    reservation = _reservation(batch, order, quantity, AllocationReservationStatus.COMMITTED)
    with pytest.raises(ValueError):
        active_reserved_quantity((reservation,), effective_at=NOW)


def test_negative_batch_and_order_quantities_are_rejected() -> None:
    batch, order = _batch(Decimal("-0.001")), _order(Decimal("-0.001"))
    with pytest.raises(ValueError):
        batch_available_kg(batch, (), effective_at=NOW)
    with pytest.raises(ValueError):
        order_remaining_kg(order, (), effective_at=NOW)
