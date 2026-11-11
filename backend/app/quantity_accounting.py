"""Read-only Decimal accounting for persisted allocation reservations."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models import AllocationReservation, AllocationReservationStatus, Batch, Order


ZERO = Decimal("0")


def _require_aware_timestamp(effective_at: datetime) -> None:
    if not isinstance(effective_at, datetime) or effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError("effective_at must be a timezone-aware datetime")


def _quantity(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= ZERO:
        raise ValueError(f"{name} must be positive")
    if not positive and value < ZERO:
        raise ValueError(f"{name} must be non-negative")
    return value


def reservation_consumes_capacity(reservation: AllocationReservation, effective_at: datetime) -> bool:
    """Return whether one reservation consumes capacity at explicit time ``effective_at``."""
    _require_aware_timestamp(effective_at)
    if reservation.status is AllocationReservationStatus.COMMITTED:
        return True
    if reservation.status is AllocationReservationStatus.PENDING:
        expires_at = reservation.expires_at
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("pending reservation expires_at must be a timezone-aware datetime")
        return expires_at > effective_at
    if reservation.status in {
        AllocationReservationStatus.REJECTED,
        AllocationReservationStatus.CANCELLED,
        AllocationReservationStatus.EXPIRED,
        AllocationReservationStatus.RELEASED,
    }:
        return False
    raise ValueError("reservation status is invalid")


def active_reserved_quantity(
    reservations: Iterable[AllocationReservation],
    *,
    effective_at: datetime,
    batch_id: UUID | None = None,
    order_id: UUID | None = None,
) -> Decimal:
    """Sum active reservation snapshots attributed through their allocation lines."""
    _require_aware_timestamp(effective_at)
    total = ZERO
    for reservation in reservations:
        if not isinstance(reservation, AllocationReservation):
            raise ValueError("reservations must contain AllocationReservation instances")
        line = reservation.allocation_line
        if line is None:
            raise ValueError("reservation must have an allocation line")
        if batch_id is not None and line.batch_id != batch_id:
            continue
        if order_id is not None and line.order_id != order_id:
            continue
        if reservation_consumes_capacity(reservation, effective_at):
            total += _quantity(reservation.reserved_quantity_kg, "reservation.reserved_quantity_kg", positive=True)
    return total


def batch_available_kg(batch: Batch, reservations: Iterable[AllocationReservation], *, effective_at: datetime) -> Decimal:
    """Return confirmed batch quantity less active reservations for this batch."""
    confirmed = _quantity(batch.confirmed_kg, "batch.confirmed_kg")
    return confirmed - active_reserved_quantity(reservations, effective_at=effective_at, batch_id=batch.id)


def order_remaining_kg(order: Order, reservations: Iterable[AllocationReservation], *, effective_at: datetime) -> Decimal:
    """Return ordered quantity less active reservations for this order."""
    ordered = _quantity(order.quantity_kg, "order.quantity_kg")
    return ordered - active_reserved_quantity(reservations, effective_at=effective_at, order_id=order.id)
