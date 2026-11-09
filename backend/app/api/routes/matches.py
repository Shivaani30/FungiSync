from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.matching_service import ELIGIBLE_ORDER_STATUSES, MatchCandidate, rank_candidates
from app.models import Batch, Order
from app.schemas import MatchCandidateOut, MatchCriterionOut, MatchResultsOut


router = APIRouter(prefix="/matches", tags=["matching"])


def _output(candidate: MatchCandidate, order: Order | None = None) -> MatchCandidateOut:
    return MatchCandidateOut(
        batch_id=UUID(candidate.batch_id), grower_id=UUID(candidate.grower_id),
        order_id=order.id if order else None, buyer_id=order.buyer_id if order else None,
        eligible=candidate.eligible, compatibility_score=candidate.compatibility_score,
        data_completeness_percentage=candidate.data_completeness_percentage,
        requested_quantity_kg=candidate.requested_quantity_kg,
        available_quantity_kg=candidate.available_quantity_kg,
        quantity_that_could_be_supplied_kg=candidate.quantity_that_could_be_supplied_kg,
        remaining_quantity_kg=candidate.remaining_quantity_kg,
        criteria={key: MatchCriterionOut(**vars(value)) for key, value in candidate.criteria.items()},
        positive_reasons=candidate.positive_reasons, unknown_criteria=candidate.unknown_criteria,
        warnings=candidate.warnings, exclusion_reasons=candidate.exclusion_reasons, rank=candidate.rank,
    )


def _filter(candidates: list[MatchCandidate], minimum_score: Decimal | None, eligible: bool | None, limit: int) -> list[MatchCandidate]:
    return [
        candidate for candidate in candidates
        if (minimum_score is None or candidate.compatibility_score >= minimum_score)
        and (eligible is None or candidate.eligible is eligible)
    ][:limit]


@router.get("/orders/{order_id}", response_model=MatchResultsOut)
def matches_for_order(
    order_id: UUID, minimum_score: Decimal | None = Query(None, ge=0, le=100),
    species: str | None = None, eligible: bool | None = None, limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    order = db.scalar(select(Order).options(selectinload(Order.buyer)).where(Order.id == order_id))
    if order is None: raise HTTPException(status_code=404, detail="Order not found")
    batches = list(db.scalars(select(Batch).options(selectinload(Batch.grower))))
    if species is not None: batches = [batch for batch in batches if batch.species.casefold() == species.strip().casefold()]
    try: candidates = rank_candidates(order, batches)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    candidates = _filter(candidates, minimum_score, eligible, limit)
    return MatchResultsOut(target_id=order.id, total_candidates=len(candidates), candidates=[_output(candidate) for candidate in candidates])


@router.get("/batches/{batch_id}", response_model=MatchResultsOut)
def matches_for_batch(
    batch_id: UUID, minimum_score: Decimal | None = Query(None, ge=0, le=100),
    species: str | None = None, eligible: bool | None = None, limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    batch = db.scalar(select(Batch).options(selectinload(Batch.grower)).where(Batch.id == batch_id))
    if batch is None: raise HTTPException(status_code=404, detail="Batch not found")
    orders = list(db.scalars(select(Order).options(selectinload(Order.buyer))))
    if species is not None: orders = [order for order in orders if order.species.casefold() == species.strip().casefold()]
    candidates: list[tuple[MatchCandidate, Order]] = []
    for order in orders:
        if order.status not in ELIGIBLE_ORDER_STATUSES: continue
        candidate = rank_candidates(order, [batch])[0]
        candidates.append((candidate, order))
    candidates.sort(key=lambda pair: (not pair[0].eligible, -pair[0].compatibility_score, -pair[0].data_completeness_percentage, str(pair[1].id)))
    filtered = [(candidate, order) for candidate, order in candidates if (minimum_score is None or candidate.compatibility_score >= minimum_score) and (eligible is None or candidate.eligible is eligible)][:limit]
    return MatchResultsOut(target_id=batch.id, total_candidates=len(filtered), candidates=[_output(candidate, order) for candidate, order in filtered])
