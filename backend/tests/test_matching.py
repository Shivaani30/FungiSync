from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import Batch, BatchStatus, Buyer, Grower, Order, OrderStatus
from app.matching_service import rank_candidates


TODAY = date(2026, 11, 10)


def make_order(**changes):
    values = dict(
        id=uuid4(), buyer_id=uuid4(), species="oyster", quantity_kg=Decimal("10"),
        price_limit=Decimal("100"), delivery_date=TODAY + timedelta(days=2),
        status=OrderStatus.PENDING, required_quality_grade="premium",
    )
    values.update(changes)
    order = Order(**values)
    order.buyer = Buyer(id=order.buyer_id, name="Buyer", location="Pune", contact="buyer@example.com")
    return order


def make_batch(**changes):
    grower_id = uuid4()
    values = dict(
        id=uuid4(), grower_id=grower_id, species="oyster", expected_kg=Decimal("10"),
        confirmed_kg=Decimal("10"), harvest_date=TODAY, best_before_date=TODAY + timedelta(days=4),
        asking_price_per_kg=Decimal("90"), quality_grade="premium", status=BatchStatus.CONFIRMED,
    )
    values.update(changes)
    batch = Batch(**values)
    batch.grower = Grower(id=grower_id, name="Grower", phone="9000000000", location="Pune")
    return batch


def test_exact_match_scores_full_when_all_data_is_known():
    candidate = rank_candidates(make_order(), [make_batch()])[0]
    assert candidate.eligible
    assert candidate.compatibility_score == Decimal("100.00")
    assert candidate.data_completeness_percentage == Decimal("100.00")
    assert candidate.rank == 1


def test_species_mismatch_is_excluded():
    candidate = rank_candidates(make_order(), [make_batch(species="shiitake")])[0]
    assert not candidate.eligible
    assert "Species mismatch" in candidate.exclusion_reasons[0]


def test_partial_and_insufficient_quantity_are_eligible_without_reservation():
    candidate = rank_candidates(make_order(), [make_batch(confirmed_kg=Decimal("4"))])[0]
    assert candidate.eligible
    assert candidate.quantity_that_could_be_supplied_kg == Decimal("4")
    assert candidate.remaining_quantity_kg == Decimal("6")
    assert candidate.criteria["quantity"].status == "partial"


def test_known_incompatible_price_quality_and_expiry_are_excluded():
    price = rank_candidates(make_order(), [make_batch(asking_price_per_kg=Decimal("101"))])[0]
    quality = rank_candidates(make_order(), [make_batch(quality_grade="standard")])[0]
    expired = rank_candidates(make_order(), [make_batch(best_before_date=TODAY + timedelta(days=1))])[0]
    assert not price.eligible and "price exceeds" in price.exclusion_reasons[0]
    assert not quality.eligible and "quality grade" in quality.exclusion_reasons[0]
    assert not expired.eligible and "best-before" in expired.exclusion_reasons[0]


def test_missing_optional_data_is_reported_and_not_counted_as_complete():
    batch = make_batch(asking_price_per_kg=None, quality_grade=None, best_before_date=None)
    order = make_order(required_quality_grade=None)
    candidate = rank_candidates(order, [batch])[0]
    assert candidate.eligible
    assert candidate.data_completeness_percentage == Decimal("40.00")
    assert len(candidate.unknown_criteria) == 3


def test_multiple_candidates_rank_by_score_then_data_completeness_then_id():
    high = make_batch()
    low = make_batch(confirmed_kg=Decimal("5"))
    no_data = make_batch(asking_price_per_kg=None, quality_grade=None, best_before_date=None)
    candidates = rank_candidates(make_order(required_quality_grade=None), [low, no_data, high])
    assert candidates[0].batch_id == str(high.id)
    assert candidates[1].batch_id == str(no_data.id)
    assert candidates[2].batch_id == str(low.id)


def test_equal_scores_use_stable_batch_id_tie_breaker():
    first, second = make_batch(), make_batch()
    candidates = rank_candidates(make_order(), [second, first])
    assert [candidate.batch_id for candidate in candidates] == sorted([str(first.id), str(second.id)])


def test_zero_or_invalid_quantities_are_not_matchable():
    zero_batch = rank_candidates(make_order(), [make_batch(confirmed_kg=Decimal("0"))])[0]
    assert not zero_batch.eligible
    with pytest.raises(ValueError, match="Order quantity must be positive"):
        rank_candidates(make_order(quantity_kg=Decimal("0")), [make_batch()])
