"""Atomic persistence for advisory allocation drafts.

This module records an advisory engine result.  It does not reserve, deduct,
lock, or otherwise make stock availability authoritative.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.allocation_engine import (
    ELIGIBLE_BATCH_STATUSES,
    ELIGIBLE_ORDER_STATUSES,
    AdvisoryAllocationLine,
    AdvisoryDraftResult,
)
from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationProposal,
    AllocationProposalStatus,
    AllocationProposalVersion,
    Batch,
    Order,
)
from app.reservation_transaction import lock_reservation_targets


ZERO = Decimal("0")
MAX_NUMERIC_12_3 = Decimal("999999999.999")


@dataclass(frozen=True)
class PersistedAdvisoryDraft:
    proposal_id: UUID
    proposal_version_id: UUID
    line_ids: tuple[UUID, ...]
    event_id: UUID


def _require_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID")
    return value


def _require_finite_decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= ZERO:
        raise ValueError(f"{name} must be positive")
    if not positive and value < ZERO:
        raise ValueError(f"{name} must be non-negative")
    return value


def _require_numeric_12_3(value: Decimal, name: str) -> None:
    if value.as_tuple().exponent < -3 or value > MAX_NUMERIC_12_3:
        raise ValueError(f"{name} must fit NUMERIC(12,3) without rounding")


def _serialize_exclusions(exclusions: object, name: str) -> list[dict[str, str]]:
    if not isinstance(exclusions, tuple):
        raise ValueError(f"{name} must be a tuple")
    serialized: list[dict[str, str]] = []
    for reason in exclusions:
        code = getattr(reason, "code", None)
        message = getattr(reason, "message", None)
        if not isinstance(code, str) or not isinstance(message, str):
            raise ValueError(f"{name} exclusions must have string code and message fields")
        serialized.append({"code": code, "message": message})
    return serialized


def _validate_result_shape(result: AdvisoryDraftResult) -> tuple[list[AdvisoryAllocationLine], set[UUID], dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    if result.advisory is not True:
        raise ValueError("Only advisory draft results can be persisted")
    _require_uuid(result.order_id, "result.order_id")
    if not isinstance(result.lines, tuple):
        raise ValueError("result.lines must be a tuple")

    lines = list(result.lines)
    line_batch_ids: set[UUID] = set()
    total = ZERO
    for index, line in enumerate(lines):
        if not isinstance(line, AdvisoryAllocationLine):
            raise ValueError(f"result.lines[{index}] must be an AdvisoryAllocationLine")
        _require_uuid(line.batch_id, f"result.lines[{index}].batch_id")
        _require_uuid(line.order_id, f"result.lines[{index}].order_id")
        if line.order_id != result.order_id:
            raise ValueError("Every allocation line must reference result.order_id")
        if line.batch_id in line_batch_ids:
            raise ValueError("Allocation line batch IDs must be unique")
        quantity = _require_finite_decimal(line.quantity_kg, f"result.lines[{index}].quantity_kg", positive=True)
        _require_numeric_12_3(quantity, f"result.lines[{index}].quantity_kg")
        line_batch_ids.add(line.batch_id)
        total += quantity

    proposed = _require_finite_decimal(result.total_proposed_kg, "result.total_proposed_kg")
    unfulfilled = _require_finite_decimal(result.unfulfilled_kg, "result.unfulfilled_kg")
    if total != proposed:
        raise ValueError("Sum of allocation line quantities must equal total_proposed_kg")

    if not isinstance(result.warnings, tuple) or not all(isinstance(warning, str) for warning in result.warnings):
        raise ValueError("result.warnings must be a tuple of strings")
    order_exclusions = _serialize_exclusions(result.order_exclusions, "result.order_exclusions")
    if not isinstance(result.batch_exclusions, dict):
        raise ValueError("result.batch_exclusions must be a dictionary")
    batch_exclusions: dict[str, list[dict[str, str]]] = {}
    exclusion_batch_ids: set[UUID] = set()
    for batch_id, exclusions in result.batch_exclusions.items():
        batch_uuid = _require_uuid(batch_id, "result.batch_exclusions key")
        exclusion_batch_ids.add(batch_uuid)
        batch_exclusions[str(batch_uuid)] = _serialize_exclusions(exclusions, "result.batch_exclusions")

    return lines, line_batch_ids | exclusion_batch_ids, batch_exclusions, order_exclusions


def persist_advisory_draft(
    session_factory: sessionmaker[Session],
    result: AdvisoryDraftResult,
    *,
    ruleset_version: str | None,
) -> PersistedAdvisoryDraft:
    """Persist one advisory Draft using a new session and one transaction."""
    lines, referenced_batch_ids, batch_exclusions, order_exclusions = _validate_result_shape(result)

    # A factory-owned session prevents a caller's autobegun transaction or pending
    # work from being committed along with this independently atomic operation.
    with session_factory.begin() as db:
        # The draft version trigger locks its proposal. Acquire existing parent
        # targets first so subsequent line inserts retain the global lock order.
        lock_reservation_targets(
            db, batch_ids=referenced_batch_ids, order_ids=(result.order_id,),
        )
        order = db.get(Order, result.order_id)
        if order is None:
            raise ValueError("Referenced order does not exist")
        if order.status not in ELIGIBLE_ORDER_STATUSES:
            raise ValueError("Referenced order is not engine-eligible")
        order_quantity = _require_finite_decimal(order.quantity_kg, "order.quantity_kg")
        if result.total_proposed_kg + result.unfulfilled_kg != order_quantity:
            raise ValueError("Proposed and unfulfilled quantities must equal the current order quantity")

        batches = {
            batch.id: batch
            for batch in db.scalars(select(Batch).where(Batch.id.in_(referenced_batch_ids)))
        } if referenced_batch_ids else {}
        missing_batch_ids = referenced_batch_ids - batches.keys()
        if missing_batch_ids:
            raise ValueError("One or more referenced batches do not exist")
        for line in lines:
            batch = batches[line.batch_id]
            if batch.status not in ELIGIBLE_BATCH_STATUSES:
                raise ValueError("Referenced line batch is not engine-eligible")
            confirmed = _require_finite_decimal(batch.confirmed_kg, "batch.confirmed_kg")
            if line.quantity_kg > confirmed:
                raise ValueError("Proposed line quantity exceeds the current confirmed batch quantity")

        proposal = AllocationProposal(
            status=AllocationProposalStatus.DRAFT,
            ruleset_version=ruleset_version,
        )
        db.add(proposal)
        db.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        db.add(version)
        db.flush()

        persisted_lines = [
            AllocationLine(
                proposal_version_id=version.id,
                batch_id=line.batch_id,
                order_id=line.order_id,
                proposed_quantity_kg=line.quantity_kg,
            )
            for line in lines
        ]
        db.add_all(persisted_lines)
        event = AllocationEvent(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            event_type="proposal_generated",
            details={
                "advisory": result.advisory,
                "order_id": str(result.order_id),
                "ruleset_version": ruleset_version,
                "lines": [
                    {
                        "batch_id": str(line.batch_id),
                        "order_id": str(line.order_id),
                        "proposed_quantity_kg": str(line.quantity_kg),
                    }
                    for line in lines
                ],
                "total_proposed_kg": str(result.total_proposed_kg),
                "unfulfilled_kg": str(result.unfulfilled_kg),
                "warnings": list(result.warnings),
                "order_exclusions": order_exclusions,
                "batch_exclusions": batch_exclusions,
            },
        )
        db.add(event)
        db.flush()
        persisted = PersistedAdvisoryDraft(
            proposal_id=proposal.id,
            proposal_version_id=version.id,
            line_ids=tuple(line.id for line in persisted_lines),
            event_id=event.id,
        )

    return persisted
