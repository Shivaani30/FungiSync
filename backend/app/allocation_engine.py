"""Pure, advisory single-order allocation draft calculation.

This module deliberately does not call Phase 2 matching, persist proposals, or
reserve stock. Controlled allocation grades are evaluated independently.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from app.models import AllocationQualityGrade, Batch, BatchStatus, Order, OrderStatus


ELIGIBLE_BATCH_STATUSES = {BatchStatus.CONFIRMED, BatchStatus.HARVESTED}
ELIGIBLE_ORDER_STATUSES = {OrderStatus.PENDING, OrderStatus.CONFIRMED}
ZERO = Decimal("0")


@dataclass(frozen=True)
class AllocationExclusion:
    code: str
    message: str


@dataclass(frozen=True)
class AdvisoryAllocationLine:
    batch_id: UUID
    order_id: UUID
    quantity_kg: Decimal
    batch_remaining_kg: Decimal


@dataclass(frozen=True)
class AdvisoryDraftResult:
    advisory: bool
    order_id: UUID
    lines: tuple[AdvisoryAllocationLine, ...]
    batch_exclusions: dict[UUID, tuple[AllocationExclusion, ...]]
    order_exclusions: tuple[AllocationExclusion, ...]
    warnings: tuple[str, ...]
    total_proposed_kg: Decimal
    unfulfilled_kg: Decimal


def _normal(value: str) -> str:
    return " ".join(value.split()).casefold()


def _quality_compatible(batch_grade: AllocationQualityGrade, order_grade: AllocationQualityGrade) -> bool:
    return {
        AllocationQualityGrade.PREMIUM: {AllocationQualityGrade.PREMIUM, AllocationQualityGrade.STANDARD},
        AllocationQualityGrade.STANDARD: {AllocationQualityGrade.STANDARD},
        AllocationQualityGrade.PROCESSING: {AllocationQualityGrade.PROCESSING},
        AllocationQualityGrade.REJECTED: set(),
    }[batch_grade].__contains__(order_grade)


def _order_exclusions(order: Order) -> list[AllocationExclusion]:
    reasons: list[AllocationExclusion] = []
    if order.status not in ELIGIBLE_ORDER_STATUSES:
        reasons.append(AllocationExclusion("ORDER_STATUS_INELIGIBLE", "Order status must be pending or confirmed."))
    if order.quantity_kg is None or Decimal(order.quantity_kg) <= ZERO:
        reasons.append(AllocationExclusion("ORDER_QUANTITY_INVALID", "Order quantity must be known and positive."))
    if order.delivery_date is None:
        reasons.append(AllocationExclusion("ORDER_DELIVERY_DATE_UNKNOWN", "Order delivery date is required."))
    if order.allocation_required_quality_grade is None:
        reasons.append(AllocationExclusion("ORDER_QUALITY_UNKNOWN", "Order controlled allocation quality grade is required."))
    return reasons


def _batch_exclusions(order: Order, batch: Batch) -> list[AllocationExclusion]:
    reasons: list[AllocationExclusion] = []
    if batch.status not in ELIGIBLE_BATCH_STATUSES:
        reasons.append(AllocationExclusion("BATCH_STATUS_INELIGIBLE", "Batch status must be confirmed or harvested."))
    if batch.confirmed_kg is None or Decimal(batch.confirmed_kg) <= ZERO:
        reasons.append(AllocationExclusion("BATCH_QUANTITY_INVALID", "Batch confirmed quantity must be known and positive."))
    if batch.asking_price_per_kg is None:
        reasons.append(AllocationExclusion("BATCH_PRICE_UNKNOWN", "Batch asking price is required."))
    if batch.harvest_date is None:
        reasons.append(AllocationExclusion("BATCH_HARVEST_DATE_UNKNOWN", "Batch harvest date is required."))
    if batch.best_before_date is None:
        reasons.append(AllocationExclusion("BATCH_BEST_BEFORE_UNKNOWN", "Batch best-before date is required."))
    if batch.allocation_quality_grade is None:
        reasons.append(AllocationExclusion("BATCH_QUALITY_UNKNOWN", "Batch controlled allocation quality grade is required."))
    if _normal(batch.species) != _normal(order.species):
        reasons.append(AllocationExclusion("SPECIES_MISMATCH", "Batch species does not match the order species."))
    if batch.harvest_date is not None and order.delivery_date is not None and batch.harvest_date > order.delivery_date:
        reasons.append(AllocationExclusion("HARVEST_AFTER_DELIVERY", "Batch harvest date is after the order delivery date."))
    if batch.best_before_date is not None and order.delivery_date is not None and batch.best_before_date < order.delivery_date:
        reasons.append(AllocationExclusion("BEST_BEFORE_BEFORE_DELIVERY", "Batch best-before date is before the order delivery date."))
    if batch.asking_price_per_kg is not None and batch.asking_price_per_kg > order.price_limit:
        reasons.append(AllocationExclusion("PRICE_ABOVE_LIMIT", "Batch asking price exceeds the order price limit."))
    if batch.allocation_quality_grade is not None and order.allocation_required_quality_grade is not None:
        if not _quality_compatible(batch.allocation_quality_grade, order.allocation_required_quality_grade):
            reasons.append(AllocationExclusion("QUALITY_INCOMPATIBLE", "Controlled allocation quality grades are incompatible."))
    return reasons


def _batch_signature(batch: Batch) -> tuple[object, ...]:
    """Only allocation-relevant values participate in duplicate conflict checks."""
    return (
        batch.status,
        batch.species,
        batch.confirmed_kg,
        batch.asking_price_per_kg,
        batch.harvest_date,
        batch.best_before_date,
        batch.allocation_quality_grade,
    )


def _unique_batches(batches: Iterable[Batch]) -> tuple[list[Batch], dict[UUID, tuple[AllocationExclusion, ...]]]:
    selected: dict[UUID, Batch] = {}
    conflicts: dict[UUID, tuple[AllocationExclusion, ...]] = {}
    for batch in batches:
        prior = selected.get(batch.id)
        if prior is None:
            selected[batch.id] = batch
        elif _batch_signature(prior) != _batch_signature(batch):
            conflicts[batch.id] = (
                AllocationExclusion(
                    "DUPLICATE_BATCH_CONFLICT",
                    "Duplicate batch ID has conflicting allocation-relevant values.",
                ),
            )
    return [batch for batch_id, batch in selected.items() if batch_id not in conflicts], conflicts


def calculate_advisory_draft(order: Order, batches: Iterable[Batch]) -> AdvisoryDraftResult:
    """Generate deterministic lines without persisting or reserving any quantity."""
    order_reasons = _order_exclusions(order)
    exclusions: dict[UUID, tuple[AllocationExclusion, ...]] = {}
    lines: list[AdvisoryAllocationLine] = []
    total = ZERO
    remaining = Decimal(order.quantity_kg) if order.quantity_kg is not None and Decimal(order.quantity_kg) > ZERO else ZERO

    if not order_reasons:
        unique_batches, conflicts = _unique_batches(batches)
        exclusions.update(conflicts)
        for batch in sorted(unique_batches, key=lambda item: str(item.id)):
            reasons = _batch_exclusions(order, batch)
            if reasons:
                exclusions[batch.id] = tuple(reasons)
                continue
            batch_remaining = Decimal(batch.confirmed_kg)
            quantity = min(batch_remaining, remaining)
            if quantity <= ZERO:
                continue
            batch_remaining -= quantity
            remaining -= quantity
            total += quantity
            lines.append(AdvisoryAllocationLine(batch.id, order.id, quantity, batch_remaining))
            if remaining == ZERO:
                break

    return AdvisoryDraftResult(
        advisory=True,
        order_id=order.id,
        lines=tuple(lines),
        batch_exclusions=exclusions,
        order_exclusions=tuple(order_reasons),
        warnings=("Advisory draft only: no stock is reserved and availability may change before approval.",),
        total_proposed_kg=total,
        unfulfilled_kg=remaining,
    )
