"""Pure, deterministic and read-only supply-demand compatibility scoring."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from app.models import Batch, BatchStatus, Buyer, Order, OrderStatus


@dataclass(frozen=True)
class MatchingWeights:
    quantity: int = 30
    freshness: int = 25
    price: int = 20
    quality: int = 15
    location: int = 10


DEFAULT_WEIGHTS = MatchingWeights()
ELIGIBLE_BATCH_STATUSES = {BatchStatus.CONFIRMED, BatchStatus.HARVESTED}
ELIGIBLE_ORDER_STATUSES = {OrderStatus.PENDING, OrderStatus.CONFIRMED}


@dataclass
class Criterion:
    status: str
    weight: int
    points: Decimal = Decimal("0")
    reason: str = ""


@dataclass
class MatchCandidate:
    batch_id: str
    grower_id: str
    eligible: bool
    compatibility_score: Decimal
    data_completeness_percentage: Decimal
    requested_quantity_kg: Decimal
    available_quantity_kg: Decimal | None
    quantity_that_could_be_supplied_kg: Decimal
    remaining_quantity_kg: Decimal
    criteria: dict[str, Criterion]
    positive_reasons: list[str] = field(default_factory=list)
    unknown_criteria: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exclusion_reasons: list[str] = field(default_factory=list)
    rank: int | None = None


def _normal(value: str) -> str:
    return " ".join(value.split()).casefold()


def _score(criteria: dict[str, Criterion]) -> tuple[Decimal, Decimal]:
    known = sum(c.weight for c in criteria.values() if c.status != "unknown")
    points = sum((c.points for c in criteria.values()), Decimal("0"))
    score = Decimal("0") if not known else points / known * 100
    return score.quantize(Decimal("0.01")), Decimal(known).quantize(Decimal("0.01"))


def _evaluate(order: Order, buyer: Buyer | None, batch: Batch, weights: MatchingWeights) -> MatchCandidate:
    excluded: list[str] = []; positive: list[str] = []; unknown: list[str] = []; warnings: list[str] = []
    requested = Decimal(order.quantity_kg)
    available = Decimal(batch.confirmed_kg) if batch.confirmed_kg is not None else None
    supplied = min(available, requested) if available is not None and available > 0 else Decimal("0")
    criteria: dict[str, Criterion] = {}
    if _normal(batch.species) == _normal(order.species):
        criteria["species"] = Criterion("matched", 0, reason="Exact normalized species match.")
        positive.append(f"Species matches: {batch.species}.")
    else:
        excluded.append(f"Species mismatch: order requires {order.species}; batch is {batch.species}.")
        criteria["species"] = Criterion("incompatible", 0, reason=excluded[-1])
    if batch.status not in ELIGIBLE_BATCH_STATUSES: excluded.append(f"Batch status {batch.status.value} is not eligible.")
    if available is None: excluded.append("Confirmed quantity is unknown; a positive confirmed quantity is required.")
    elif available <= 0: excluded.append("Confirmed quantity must be positive.")
    if available is not None and available > 0:
        coverage = supplied / requested
        criteria["quantity"] = Criterion("matched" if coverage >= 1 else "partial", weights.quantity, Decimal(weights.quantity) * coverage, f"Can supply {supplied} kg of {requested} kg requested.")
        positive.append(criteria["quantity"].reason)
    else:
        criteria["quantity"] = Criterion("unknown", weights.quantity, reason="No positive confirmed quantity.")
        unknown.append("Positive confirmed quantity is unavailable.")
    if batch.harvest_date > order.delivery_date: excluded.append("Harvest date is after the requested delivery date.")
    if batch.best_before_date is None:
        criteria["freshness"] = Criterion("unknown", weights.freshness, reason="Best-before date is not recorded.")
        unknown.append("Freshness cannot be established because best-before date is unknown.")
    elif order.delivery_date > batch.best_before_date:
        excluded.append("Delivery date is after the batch best-before date.")
        criteria["freshness"] = Criterion("incompatible", weights.freshness, reason=excluded[-1])
    else:
        criteria["freshness"] = Criterion("matched", weights.freshness, Decimal(weights.freshness), "Delivery is on or before best-before date.")
        positive.append(criteria["freshness"].reason)
    if batch.asking_price_per_kg is None:
        criteria["price"] = Criterion("unknown", weights.price, reason="Batch asking price is not recorded."); unknown.append("Price compatibility is unknown.")
    elif batch.asking_price_per_kg > order.price_limit:
        excluded.append("Batch asking price exceeds the buyer price limit."); criteria["price"] = Criterion("incompatible", weights.price, reason=excluded[-1])
    else:
        criteria["price"] = Criterion("matched", weights.price, Decimal(weights.price), "Batch asking price is within buyer price limit."); positive.append(criteria["price"].reason)
    if batch.quality_grade is None or order.required_quality_grade is None:
        criteria["quality"] = Criterion("unknown", weights.quality, reason="Batch or required quality grade is not recorded."); unknown.append("Quality compatibility is unknown.")
    elif _normal(batch.quality_grade) != _normal(order.required_quality_grade):
        excluded.append("Batch quality grade does not equal the buyer required quality grade."); criteria["quality"] = Criterion("incompatible", weights.quality, reason=excluded[-1])
    else:
        criteria["quality"] = Criterion("matched", weights.quality, Decimal(weights.quality), "Batch quality exactly matches the required quality grade."); positive.append(criteria["quality"].reason)
    grower = batch.grower
    if grower is None or buyer is None:
        criteria["location"] = Criterion("unknown", weights.location, reason="Grower or buyer location is unavailable."); unknown.append("Location compatibility is unknown.")
    elif _normal(grower.location) == _normal(buyer.location):
        criteria["location"] = Criterion("matched", weights.location, Decimal(weights.location), "Grower and buyer locations exactly match."); positive.append(criteria["location"].reason)
    else:
        criteria["location"] = Criterion("different", weights.location, reason="Locations differ; no geographic distance is inferred."); warnings.append(criteria["location"].reason)
    score, completeness = _score(criteria)
    return MatchCandidate(str(batch.id), str(batch.grower_id), not excluded, score if not excluded else Decimal("0.00"), completeness, requested, available, supplied, requested - supplied, criteria, positive, unknown, warnings, excluded)


def rank_candidates(order: Order, batches: Iterable[Batch], buyer: Buyer | None = None, weights: MatchingWeights = DEFAULT_WEIGHTS) -> list[MatchCandidate]:
    """Evaluate candidates without mutating models, sessions, or database state."""
    if order.status not in ELIGIBLE_ORDER_STATUSES: raise ValueError(f"Order status {order.status.value} is not eligible for matching.")
    if order.quantity_kg <= 0: raise ValueError("Order quantity must be positive for matching.")
    candidates = [_evaluate(order, buyer or order.buyer, batch, weights) for batch in batches]
    candidates.sort(key=lambda c: (not c.eligible, -c.compatibility_score, -c.data_completeness_percentage, c.batch_id))
    for index, candidate in enumerate((c for c in candidates if c.eligible), 1): candidate.rank = index
    return candidates
