"""Narrow transaction and locking primitives for future reservation operations.

This module deliberately does not create or transition reservations.  A future
service must use the staged protocol below inside :func:`run_reservation_transaction`:

1. Discover target batch, order, proposal, and allocation-line IDs.
2. Lock those parent rows with :func:`lock_reservation_targets`.
3. Lock discovered allocation lines with :func:`lock_allocation_lines`.
4. While those locks remain held, discover relevant reservation IDs again.
5. Lock only those reservations in a second :func:`lock_reservation_targets`
   call.
6. Re-read all locked records, then capture one effective database timestamp.

Reservation IDs discovered before parent locks are not complete under concurrent
writes.  Parent locks serialize the rediscovery window; a reservation-only lock
does not protect an empty or stale reservation set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.models import AllocationLine, AllocationProposal, AllocationReservation, Batch, Order


T = TypeVar("T")

RETRYABLE_POSTGRES_SQLSTATES = frozenset({"40001", "40P01", "55P03"})


class MissingLockedRecordsError(ValueError):
    """Raised when caller-discovered required records disappear before locking."""


@dataclass(frozen=True)
class LockedReservationTargets:
    """Records locked/re-read in the reservation operation's global order."""

    batches: tuple[Batch, ...]
    orders: tuple[Order, ...]
    proposals: tuple[AllocationProposal, ...]
    reservations: tuple[AllocationReservation, ...]


def run_reservation_transaction(
    session_factory: sessionmaker[Session],
    operation: Callable[[Session], T],
) -> T:
    """Run ``operation`` in a new factory-owned session and atomic transaction.

    ``session_factory.begin()`` commits only this fresh session on success and
    rolls it back and closes it when the operation, flush, or commit fails.
    Callers must not pass a request-owned session into this boundary.
    """
    with session_factory.begin() as session:
        return operation(session)


def _ordered_ids(ids: Iterable[UUID], name: str) -> tuple[UUID, ...]:
    values = tuple(ids)
    if not all(isinstance(value, UUID) for value in values):
        raise ValueError(f"{name} must contain UUID values")
    return tuple(sorted(set(values)))


def _select_required_for_update(
    session: Session,
    model: type[Batch] | type[Order] | type[AllocationProposal] | type[AllocationLine] | type[AllocationReservation],
    ids: Iterable[UUID],
    name: str,
) -> tuple[object, ...]:
    """Lock exactly the supplied records, sorted by UUID, and refresh identity-map state."""
    ordered_ids = _ordered_ids(ids, name)
    if not ordered_ids:
        return ()
    statement: Select[tuple[object]] = (
        select(model)
        .where(model.id.in_(ordered_ids))
        .order_by(model.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    records = tuple(session.scalars(statement))
    found_ids = {record.id for record in records}
    missing = set(ordered_ids) - found_ids
    if missing:
        formatted = ", ".join(str(value) for value in sorted(missing))
        raise MissingLockedRecordsError(f"Required {name} records do not exist: {formatted}")
    return records


def lock_reservation_targets(
    session: Session,
    *,
    batch_ids: Iterable[UUID] = (),
    order_ids: Iterable[UUID] = (),
    proposal_ids: Iterable[UUID] = (),
    reservation_ids: Iterable[UUID] = (),
) -> LockedReservationTargets:
    """Lock caller-supplied targets in the global reservation lock order.

    Each category uses its own ordered query.  The complete order is batches,
    orders, proposals, allocation lines, then reservations.  Callers must lock
    lines with :func:`lock_allocation_lines` after this parent-lock call and
    before rediscovering reservations.  Parent locks serialize cooperating
    inserts; reservation rows alone cannot protect an empty reservation set.
    The second call must leave ``batch_ids``, ``order_ids``, and
    ``proposal_ids`` empty, so it cannot reacquire an earlier lock category.
    """
    batches = _select_required_for_update(session, Batch, batch_ids, "batch")
    orders = _select_required_for_update(session, Order, order_ids, "order")
    proposals = _select_required_for_update(session, AllocationProposal, proposal_ids, "proposal")
    reservations = _select_required_for_update(session, AllocationReservation, reservation_ids, "reservation")
    return LockedReservationTargets(
        batches=tuple(batches),  # type: ignore[arg-type]
        orders=tuple(orders),  # type: ignore[arg-type]
        proposals=tuple(proposals),  # type: ignore[arg-type]
        reservations=tuple(reservations),  # type: ignore[arg-type]
    )


def lock_allocation_lines(session: Session, allocation_line_ids: Iterable[UUID]) -> tuple[AllocationLine, ...]:
    """Lock caller-discovered allocation lines in ascending UUID order.

    This is the fourth reservation-operation lock category, after proposal
    locks and before reservation locks.  Missing IDs are never silently
    skipped; an empty input is safe and produces no SQL query.
    """
    records = _select_required_for_update(session, AllocationLine, allocation_line_ids, "allocation line")
    return tuple(records)  # type: ignore[return-value]


def reread_locked_targets(
    session: Session,
    *,
    batch_ids: Iterable[UUID] = (),
    order_ids: Iterable[UUID] = (),
    proposal_ids: Iterable[UUID] = (),
    reservation_ids: Iterable[UUID] = (),
) -> LockedReservationTargets:
    """Expire pre-lock ORM state and re-read records whose locks are already held.

    Call this only after parent, allocation-line, and post-line-lock reservation
    discovery/locking are complete.  It does not acquire a new lock category and
    must precede reservation validation, accounting, and
    :func:`effective_timestamp`.
    """
    session.expire_all()

    def reread(model: type[object], ids: Iterable[UUID], name: str) -> tuple[object, ...]:
        ordered_ids = _ordered_ids(ids, name)
        if not ordered_ids:
            return ()
        records = tuple(session.scalars(
            select(model)
            .where(model.id.in_(ordered_ids))
            .order_by(model.id.asc())
            .execution_options(populate_existing=True)
        ))
        found_ids = {record.id for record in records}
        missing = set(ordered_ids) - found_ids
        if missing:
            formatted = ", ".join(str(value) for value in sorted(missing))
            raise MissingLockedRecordsError(f"Required {name} records do not exist: {formatted}")
        return records

    return LockedReservationTargets(
        batches=tuple(reread(Batch, batch_ids, "batch")),  # type: ignore[arg-type]
        orders=tuple(reread(Order, order_ids, "order")),  # type: ignore[arg-type]
        proposals=tuple(reread(AllocationProposal, proposal_ids, "proposal")),  # type: ignore[arg-type]
        reservations=tuple(reread(AllocationReservation, reservation_ids, "reservation")),  # type: ignore[arg-type]
    )


def effective_timestamp(session: Session) -> datetime:
    """Get one database-clock timestamp after every required lock is acquired."""
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PostgreSQL clock_timestamp() must return a timezone-aware datetime")
    return value


def postgres_sqlstate(error: BaseException) -> str | None:
    """Return a DBAPI SQLSTATE when available without classifying other failures."""
    candidate: object | None = error.orig if isinstance(error, DBAPIError) else error
    value = getattr(candidate, "sqlstate", None)
    return value if isinstance(value, str) else None


def is_retryable_postgres_failure(error: BaseException) -> bool:
    """Identify selected transient PostgreSQL failures; this function never retries."""
    return postgres_sqlstate(error) in RETRYABLE_POSTGRES_SQLSTATES
