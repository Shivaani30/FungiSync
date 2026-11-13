"""Read-only HTTP adapter for the fixed synthetic harvest demonstration."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from app.harvest_replanning import build_synthetic_harvest_replan
from app.schemas import SyntheticHarvestReplanOut


router = APIRouter(prefix="/harvest-coordination/demo", tags=["harvest coordination"])


@router.get(
    "/replan",
    response_model=SyntheticHarvestReplanOut,
    summary="Get the synthetic harvest replan demonstration",
    responses={422: {"description": "Query parameters are not supported"}},
    description=(
        "Returns a fixed synthetic 60 kg scenario for demonstration only. "
        "No operational records are read or changed, and the alternate buyer "
        "quantity is a proposal rather than a commitment."
    ),
)
def get_synthetic_harvest_replan(request: Request) -> SyntheticHarvestReplanOut:
    """Return the fixed fixture; reject query parameters to avoid ambiguity."""
    if request.query_params:
        raise HTTPException(status_code=422, detail="Query parameters are not supported")

    return SyntheticHarvestReplanOut.model_validate(
        asdict(build_synthetic_harvest_replan())
    )
