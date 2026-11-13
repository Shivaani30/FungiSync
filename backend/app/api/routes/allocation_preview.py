"""Read-only operational allocation previews; never persist or reserve results."""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.allocation_preview import (
    AllocationPreviewCapacityError,
    calculate_reservation_aware_preview,
)
from app.db.session import get_db
from app.models import AllocationLine, AllocationReservation, Batch, Order
from app.schemas import AllocationPreviewOut


router = APIRouter(prefix="/allocation-preview", tags=["allocation preview"])


@router.get(
    "/orders/{order_id}",
    response_model=AllocationPreviewOut,
    summary="Preview an advisory allocation for an operational order",
    description=(
        "Calculates a point-in-time, non-persisted allocation using confirmed "
        "batch and order quantities less active reservations. It does not create "
        "a proposal, reservation, approval, or buyer commitment. This unauthenticated "
        "API is intended only for a controlled local hackathon environment and "
        "must not be exposed to untrusted networks."
    ),
)
def preview_order_allocation(
    order_id: UUID,
    db: Session = Depends(get_db),
) -> AllocationPreviewOut:
    # Freeze all reads to one database snapshot and make the request transaction
    # read-only. transaction_timestamp() is conservative for pending expiries:
    # it cannot exclude a reservation that expires just after the snapshot begins.
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    batches = tuple(db.scalars(select(Batch).order_by(Batch.id.asc())))
    reservation_predicates = [AllocationLine.order_id == order.id]
    if batches:
        batch_ids = tuple(batch.id for batch in batches)
        reservation_predicates.append(AllocationLine.batch_id.in_(batch_ids))
    reservations = tuple(
        db.scalars(
            select(AllocationReservation)
            .join(
                AllocationLine,
                AllocationReservation.allocation_line_id == AllocationLine.id,
            )
            .where(or_(*reservation_predicates))
            .options(selectinload(AllocationReservation.allocation_line))
            .order_by(AllocationReservation.id.asc())
        )
    )

    effective_at = db.scalar(select(func.transaction_timestamp()))
    if (
        not isinstance(effective_at, datetime)
        or effective_at.tzinfo is None
        or effective_at.utcoffset() is None
    ):
        raise HTTPException(status_code=503, detail="Database snapshot time is unavailable")
    try:
        preview = calculate_reservation_aware_preview(
            order,
            batches,
            reservations,
            effective_at=effective_at,
        )
        return AllocationPreviewOut.model_validate(asdict(preview))
    except AllocationPreviewCapacityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Current allocation quantity data is inconsistent; preview is unavailable",
        ) from error
