"""Read-only, reservation-aware previews using the advisory allocation engine."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.allocation_engine import calculate_advisory_draft
from app.models import (
    AllocationReservation,
    Batch,
    Order,
)
from app.quantity_accounting import (
    active_reserved_quantity,
    batch_available_kg,
    order_remaining_kg,
)


QUANTITY_BASIS: Literal[
    "confirmed_batch_and_order_quantities_less_active_reservations"
] = "confirmed_batch_and_order_quantities_less_active_reservations"


class AllocationPreviewCapacityError(ValueError):
    """Persisted reservations exceed a batch or order's recorded quantity."""


@dataclass(frozen=True)
class PreviewExclusion:
    code: str
    message: str


@dataclass(frozen=True)
class PreviewLine:
    batch_id: UUID
    batch_confirmed_kg: Decimal | None
    active_reserved_kg: Decimal
    remaining_before_preview_kg: Decimal | None
    proposed_quantity_kg: Decimal
    remaining_after_preview_kg: Decimal | None


@dataclass(frozen=True)
class ExcludedBatch:
    batch_id: UUID
    batch_confirmed_kg: Decimal | None
    active_reserved_kg: Decimal
    remaining_before_preview_kg: Decimal | None
    reasons: tuple[PreviewExclusion, ...]


@dataclass(frozen=True)
class AllocationPreview:
    order_id: UUID
    is_advisory: Literal[True]
    is_synthetic: Literal[False]
    persisted: Literal[False]
    reservation_committed: Literal[False]
    buyer_commitment_created: Literal[False]
    quantity_basis: Literal["confirmed_batch_and_order_quantities_less_active_reservations"]
    reservation_accounting_applied: Literal[True]
    effective_at: datetime
    total_requested_kg: Decimal
    active_reserved_order_kg: Decimal
    remaining_order_kg: Decimal
    total_allocated_kg: Decimal
    total_unfulfilled_kg: Decimal
    allocation_lines: tuple[PreviewLine, ...]
    excluded_batches: tuple[ExcludedBatch, ...]
    order_exclusions: tuple[PreviewExclusion, ...]
    warnings: tuple[str, ...]
    is_reconciled: Literal[True]


def _preview_batch(batch: Batch, remaining_kg: Decimal | None) -> Batch:
    """Build a transient engine input; never alter the session-loaded batch."""
    return Batch(
        id=batch.id,
        grower_id=batch.grower_id,
        species=batch.species,
        expected_kg=batch.expected_kg,
        confirmed_kg=remaining_kg,
        harvest_date=batch.harvest_date,
        asking_price_per_kg=batch.asking_price_per_kg,
        quality_grade=batch.quality_grade,
        allocation_quality_grade=batch.allocation_quality_grade,
        best_before_date=batch.best_before_date,
        status=batch.status,
    )


def _preview_order(order: Order, remaining_kg: Decimal) -> Order:
    """Build a transient engine input; never alter the session-loaded order."""
    return Order(
        id=order.id,
        buyer_id=order.buyer_id,
        species=order.species,
        quantity_kg=remaining_kg,
        price_limit=order.price_limit,
        delivery_date=order.delivery_date,
        required_quality_grade=order.required_quality_grade,
        allocation_required_quality_grade=order.allocation_required_quality_grade,
        status=order.status,
    )


def calculate_reservation_aware_preview(
    order: Order,
    batches: tuple[Batch, ...] | list[Batch],
    reservations: tuple[AllocationReservation, ...] | list[AllocationReservation],
    *,
    effective_at: datetime,
) -> AllocationPreview:
    """Calculate a non-persisted allocation from remaining quantities.

    The time is supplied by the caller so pending-reservation expiry is
    evaluated consistently for every batch and the selected order.
    """
    active_order_reserved = active_reserved_quantity(
        reservations, effective_at=effective_at, order_id=order.id
    )
    remaining_order = order_remaining_kg(
        order, reservations, effective_at=effective_at
    )
    if remaining_order < 0:
        raise AllocationPreviewCapacityError(
            "Active reservations exceed the order quantity; preview is unavailable"
        )

    batch_remaining: dict[UUID, Decimal | None] = {}
    batch_reserved: dict[UUID, Decimal] = {}
    engine_batches: list[Batch] = []
    for batch in batches:
        reserved = active_reserved_quantity(
            reservations, effective_at=effective_at, batch_id=batch.id
        )
        batch_reserved[batch.id] = reserved
        if batch.confirmed_kg is None:
            available = None
        else:
            available = batch_available_kg(
                batch, reservations, effective_at=effective_at
            )
            if available < 0:
                raise AllocationPreviewCapacityError(
                    f"Active reservations exceed confirmed quantity for batch {batch.id}; preview is unavailable"
                )
        batch_remaining[batch.id] = available
        engine_batches.append(_preview_batch(batch, available))

    engine_order = _preview_order(order, remaining_order)
    draft = calculate_advisory_draft(engine_order, engine_batches)

    allocation_lines = tuple(
        PreviewLine(
            batch_id=line.batch_id,
            batch_confirmed_kg=next(
                batch.confirmed_kg for batch in batches if batch.id == line.batch_id
            ),
            active_reserved_kg=batch_reserved[line.batch_id],
            remaining_before_preview_kg=batch_remaining[line.batch_id],
            proposed_quantity_kg=line.quantity_kg,
            remaining_after_preview_kg=(
                batch_remaining[line.batch_id] - line.quantity_kg
                if batch_remaining[line.batch_id] is not None
                else None
            ),
        )
        for line in draft.lines
    )
    excluded_batches = tuple(
        ExcludedBatch(
            batch_id=batch_id,
            batch_confirmed_kg=next(
                batch.confirmed_kg for batch in batches if batch.id == batch_id
            ),
            active_reserved_kg=batch_reserved[batch_id],
            remaining_before_preview_kg=batch_remaining[batch_id],
            reasons=tuple(PreviewExclusion(reason.code, reason.message) for reason in reasons),
        )
        for batch_id, reasons in sorted(draft.batch_exclusions.items(), key=lambda item: str(item[0]))
    )
    order_exclusions = tuple(
        PreviewExclusion(reason.code, reason.message)
        for reason in draft.order_exclusions
    )

    if draft.total_proposed_kg + draft.unfulfilled_kg != remaining_order:
        raise ValueError("Allocation engine output does not reconcile to remaining order quantity")

    warnings = tuple(draft.warnings) + (
        "Active unexpired PENDING and all COMMITTED reservations are deducted; terminal and expired PENDING reservations are not.",
        "This is a point-in-time advisory preview. Quantities may change after the response; no capacity is reserved.",
    )
    return AllocationPreview(
        order_id=order.id,
        is_advisory=True,
        is_synthetic=False,
        persisted=False,
        reservation_committed=False,
        buyer_commitment_created=False,
        quantity_basis=QUANTITY_BASIS,
        reservation_accounting_applied=True,
        effective_at=effective_at,
        total_requested_kg=order.quantity_kg,
        active_reserved_order_kg=active_order_reserved,
        remaining_order_kg=remaining_order,
        total_allocated_kg=draft.total_proposed_kg,
        total_unfulfilled_kg=draft.unfulfilled_kg,
        allocation_lines=allocation_lines,
        excluded_batches=excluded_batches,
        order_exclusions=order_exclusions,
        warnings=warnings,
        is_reconciled=True,
    )
