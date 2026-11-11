"""Reservation-aware updates for allocation-sensitive batch and order fields."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    AllocationLine,
    AllocationProposalVersion,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    Order,
)
from app.quantity_accounting import active_reserved_quantity
from app.reservation_transaction import (
    effective_timestamp,
    lock_allocation_lines,
    lock_reservation_targets,
    reread_locked_targets,
    run_reservation_transaction,
)


class MutationTargetNotFoundError(ValueError):
    """The batch or order being updated does not exist."""


class MutationTargetsChangedError(ValueError):
    """Related allocation targets changed during lock acquisition."""


class ReservationMutationConflictError(ValueError):
    """The requested change conflicts with unresolved reservations."""


_BATCH_ELIGIBILITY = frozenset({
    "status", "species", "allocation_quality_grade", "harvest_date",
    "asking_price_per_kg", "best_before_date",
})
_ORDER_ELIGIBILITY = frozenset({
    "status", "species", "allocation_required_quality_grade", "price_limit",
    "delivery_date",
})


def _line_targets(session: Session, target_id: UUID, *, batch: bool) -> tuple[tuple[UUID, UUID, UUID, UUID, UUID, Decimal], ...]:
    target_column = AllocationLine.batch_id if batch else AllocationLine.order_id
    return tuple(session.execute(
        select(
            AllocationLine.id,
            AllocationProposalVersion.proposal_id,
            AllocationLine.proposal_version_id,
            AllocationLine.batch_id,
            AllocationLine.order_id,
            AllocationLine.proposed_quantity_kg,
        )
        .join(AllocationProposalVersion, AllocationLine.proposal_version_id == AllocationProposalVersion.id)
        .where(target_column == target_id)
        .order_by(AllocationLine.id.asc())
    ))


def _reservation_ids(session: Session, target_id: UUID, *, batch: bool) -> tuple[UUID, ...]:
    target_column = AllocationLine.batch_id if batch else AllocationLine.order_id
    return tuple(session.scalars(
        select(AllocationReservation.id)
        .join(AllocationLine, AllocationReservation.allocation_line_id == AllocationLine.id)
        .where(target_column == target_id)
        .order_by(AllocationReservation.id.asc())
    ))


def _update(
    session_factory: sessionmaker[Session], target_id: UUID, values: dict,
    *, batch: bool,
) -> Batch | Order:
    if not isinstance(target_id, UUID):
        raise ValueError("target_id must be a UUID")
    model = Batch if batch else Order
    eligibility_fields = _BATCH_ELIGIBILITY if batch else _ORDER_ELIGIBILITY
    quantity_field = "confirmed_kg" if batch else "quantity_kg"

    def operation(session: Session) -> Batch | Order:
        if session.get(model, target_id) is None:
            raise MutationTargetNotFoundError(f"{model.__name__} not found")
        discovered_lines = _line_targets(session, target_id, batch=batch)
        batch_ids = (target_id,) if batch else tuple(sorted({row[3] for row in discovered_lines}))
        order_ids = tuple(sorted({row[4] for row in discovered_lines})) if batch else (target_id,)

        # Parent first: its FOR UPDATE lock prevents cooperating line inserts or
        # target changes while the remaining lock categories are acquired.
        lock_reservation_targets(session, batch_ids=batch_ids, order_ids=order_ids)
        if _line_targets(session, target_id, batch=batch) != discovered_lines:
            raise MutationTargetsChangedError("Allocation targets changed while locking the parent")
        proposal_ids = tuple(sorted({row[1] for row in discovered_lines}))
        line_ids = tuple(row[0] for row in discovered_lines)
        lock_reservation_targets(session, proposal_ids=proposal_ids)
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise MutationTargetsChangedError("Allocation line targets changed while locking")
        if _line_targets(session, target_id, batch=batch) != discovered_lines:
            raise MutationTargetsChangedError("Allocation targets changed while locking lines")

        reservation_ids = _reservation_ids(session, target_id, batch=batch)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(
            session, batch_ids=batch_ids, order_ids=order_ids,
            proposal_ids=proposal_ids, reservation_ids=reservation_ids,
        )
        if _line_targets(session, target_id, batch=batch) != discovered_lines:
            raise MutationTargetsChangedError("Allocation targets changed after locking")
        if _reservation_ids(session, target_id, batch=batch) != reservation_ids:
            raise MutationTargetsChangedError("Reservation targets changed after locking")

        target = reread.batches[0] if batch else reread.orders[0]
        reservations = reread.reservations
        # Re-read each line while all locks are held.  This avoids stale ORM
        # relationships and verifies that every reservation still targets us.
        lines_by_id = {line.id: line for line in locked_lines}
        for reservation in reservations:
            line = lines_by_id.get(reservation.allocation_line_id)
            linked_line = reservation.allocation_line
            if (
                line is None or linked_line is None or linked_line.id != line.id
                or (linked_line.batch_id if batch else linked_line.order_id) != target_id
            ):
                raise MutationTargetsChangedError("Reservation allocation target changed")

        effective_at = effective_timestamp(session)
        changed = {field for field, value in values.items() if value != getattr(target, field)}
        if changed & eligibility_fields and any(
            reservation.status in {AllocationReservationStatus.PENDING, AllocationReservationStatus.COMMITTED}
            for reservation in reservations
        ):
            raise ReservationMutationConflictError("Eligibility fields cannot change with unresolved reservations")
        if quantity_field in changed:
            proposed = values[quantity_field]
            reserved = active_reserved_quantity(
                reservations, effective_at=effective_at,
                **({"batch_id": target_id} if batch else {"order_id": target_id}),
            )
            if reserved > 0 and (not isinstance(proposed, Decimal) or proposed < reserved):
                raise ReservationMutationConflictError("Quantity cannot be lower than active reservations")

        for field, value in values.items():
            setattr(target, field, value)
        session.flush()
        session.refresh(target)
        session.expunge(target)
        return target

    return run_reservation_transaction(session_factory, operation)


def update_batch_reservation_aware(session_factory: sessionmaker[Session], batch_id: UUID, values: dict) -> Batch:
    return _update(session_factory, batch_id, values, batch=True)  # type: ignore[return-value]


def update_order_reservation_aware(session_factory: sessionmaker[Session], order_id: UUID, values: dict) -> Order:
    return _update(session_factory, order_id, values, batch=False)  # type: ignore[return-value]
