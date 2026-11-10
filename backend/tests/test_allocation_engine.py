from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.allocation_engine import calculate_advisory_draft
from app.models import AllocationQualityGrade, Batch, BatchStatus, Order, OrderStatus


TODAY = date(2026, 11, 10)


def order(**changes):
    values = dict(id=UUID(int=100), buyer_id=UUID(int=1), species="oyster", quantity_kg=Decimal("10.000"), price_limit=Decimal("100.00"), delivery_date=TODAY + timedelta(days=2), status=OrderStatus.PENDING, allocation_required_quality_grade=AllocationQualityGrade.PREMIUM, required_quality_grade="legacy")
    values.update(changes)
    return Order(**values)


def batch(number=1, **changes):
    values = dict(id=UUID(int=number), grower_id=UUID(int=2), species="oyster", expected_kg=Decimal("10.000"), confirmed_kg=Decimal("10.000"), harvest_date=TODAY, best_before_date=TODAY + timedelta(days=4), asking_price_per_kg=Decimal("90.00"), status=BatchStatus.CONFIRMED, allocation_quality_grade=AllocationQualityGrade.PREMIUM, quality_grade="unrelated legacy text")
    values.update(changes)
    return Batch(**values)


@pytest.mark.parametrize(("batch_grade", "order_grade", "eligible"), [
    (AllocationQualityGrade.PREMIUM, AllocationQualityGrade.PREMIUM, True),
    (AllocationQualityGrade.PREMIUM, AllocationQualityGrade.STANDARD, True),
    (AllocationQualityGrade.STANDARD, AllocationQualityGrade.STANDARD, True),
    (AllocationQualityGrade.STANDARD, AllocationQualityGrade.PREMIUM, False),
    (AllocationQualityGrade.PROCESSING, AllocationQualityGrade.PROCESSING, True),
    (AllocationQualityGrade.REJECTED, AllocationQualityGrade.PREMIUM, False),
])
def test_controlled_quality_matrix(batch_grade, order_grade, eligible):
    result = calculate_advisory_draft(order(allocation_required_quality_grade=order_grade), [batch(allocation_quality_grade=batch_grade)])
    assert bool(result.lines) is eligible


@pytest.mark.parametrize("field,value,code", [
    ("allocation_quality_grade", None, "BATCH_QUALITY_UNKNOWN"), ("species", "shiitake", "SPECIES_MISMATCH"),
    ("status", BatchStatus.EXPECTED, "BATCH_STATUS_INELIGIBLE"), ("confirmed_kg", None, "BATCH_QUANTITY_INVALID"),
    ("confirmed_kg", Decimal("0"), "BATCH_QUANTITY_INVALID"), ("asking_price_per_kg", None, "BATCH_PRICE_UNKNOWN"),
    ("asking_price_per_kg", Decimal("101"), "PRICE_ABOVE_LIMIT"), ("harvest_date", None, "BATCH_HARVEST_DATE_UNKNOWN"),
    ("best_before_date", None, "BATCH_BEST_BEFORE_UNKNOWN"), ("harvest_date", TODAY + timedelta(days=3), "HARVEST_AFTER_DELIVERY"),
    ("best_before_date", TODAY + timedelta(days=1), "BEST_BEFORE_BEFORE_DELIVERY"),
])
def test_batch_exclusions(field, value, code):
    result = calculate_advisory_draft(order(), [batch(**{field: value})])
    assert code in {reason.code for reason in result.batch_exclusions[UUID(int=1)]}


@pytest.mark.parametrize("changes,code", [
    ({"status": OrderStatus.FULFILLED}, "ORDER_STATUS_INELIGIBLE"),
    ({"quantity_kg": None}, "ORDER_QUANTITY_INVALID"), ({"quantity_kg": Decimal("0")}, "ORDER_QUANTITY_INVALID"),
    ({"delivery_date": None}, "ORDER_DELIVERY_DATE_UNKNOWN"), ({"allocation_required_quality_grade": None}, "ORDER_QUALITY_UNKNOWN"),
])
def test_order_exclusions(changes, code):
    result = calculate_advisory_draft(order(**changes), [batch()])
    assert code in {reason.code for reason in result.order_exclusions}
    assert not result.lines


def test_single_multi_batch_partial_and_deterministic_drafts_do_not_mutate_models():
    first = batch(2, confirmed_kg=Decimal("3.333"), quality_grade="Grade A")
    second = batch(1, confirmed_kg=Decimal("4.444"), quality_grade="Export Quality")
    target = order(quantity_kg=Decimal("10.000"), required_quality_grade="anything")
    before = (first.confirmed_kg, second.confirmed_kg, target.quantity_kg)
    result = calculate_advisory_draft(target, [first, second])
    reverse = calculate_advisory_draft(target, [second, first])
    assert [line.batch_id for line in result.lines] == [UUID(int=1), UUID(int=2)]
    assert result.lines == reverse.lines
    assert result.total_proposed_kg == Decimal("7.777")
    assert result.unfulfilled_kg == Decimal("2.223")
    assert all(isinstance(line.quantity_kg, Decimal) for line in result.lines)
    assert (first.confirmed_kg, second.confirmed_kg, target.quantity_kg) == before
    assert result.advisory and "no stock is reserved" in result.warnings[0]


def test_line_never_exceeds_batch_or_order_quantity():
    result = calculate_advisory_draft(order(quantity_kg=Decimal("1.234")), [batch(confirmed_kg=Decimal("9.999"))])
    assert result.lines[0].quantity_kg == Decimal("1.234")
    assert result.total_proposed_kg <= Decimal("1.234")
    assert result.unfulfilled_kg == Decimal("0")


def test_duplicate_batch_ids_are_deduplicated_and_conflicts_are_excluded():
    original = batch(1, confirmed_kg=Decimal("4.000"))
    equivalent = batch(1, confirmed_kg=Decimal("4.000"))
    result = calculate_advisory_draft(order(quantity_kg=Decimal("10.000")), [original, original, equivalent])
    assert result.total_proposed_kg == Decimal("4.000")
    assert len(result.lines) == 1

    conflicting = batch(1, confirmed_kg=Decimal("5.000"))
    conflict_result = calculate_advisory_draft(order(), [original, conflicting])
    assert not conflict_result.lines
    assert conflict_result.batch_exclusions[UUID(int=1)][0].code == "DUPLICATE_BATCH_CONFLICT"


def test_duplicate_batch_ids_with_conflicting_eligibility_are_excluded():
    result = calculate_advisory_draft(order(), [batch(1), batch(1, status=BatchStatus.CANCELLED)])
    assert result.batch_exclusions[UUID(int=1)][0].code == "DUPLICATE_BATCH_CONFLICT"


def test_empty_harvested_confirmed_and_cancelled_status_behaviors():
    assert calculate_advisory_draft(order(), []).unfulfilled_kg == Decimal("10.000")
    assert calculate_advisory_draft(order(), [batch(status=BatchStatus.HARVESTED)]).lines
    assert calculate_advisory_draft(order(status=OrderStatus.CONFIRMED), [batch()]).lines
    assert "BATCH_STATUS_INELIGIBLE" in {x.code for x in calculate_advisory_draft(order(), [batch(status=BatchStatus.CANCELLED)]).batch_exclusions[UUID(int=1)]}
    assert "ORDER_STATUS_INELIGIBLE" in {x.code for x in calculate_advisory_draft(order(status=OrderStatus.CANCELLED), [batch()]).order_exclusions}


def test_negative_quantities_and_ineligible_order_skip_batch_evaluation():
    assert "BATCH_QUANTITY_INVALID" in {x.code for x in calculate_advisory_draft(order(), [batch(confirmed_kg=Decimal("-1"))]).batch_exclusions[UUID(int=1)]}
    result = calculate_advisory_draft(order(quantity_kg=Decimal("-1")), [batch(species="shiitake")])
    assert result.order_exclusions and not result.batch_exclusions and not result.lines


@pytest.mark.parametrize("batch_grade,order_grade", [
    (AllocationQualityGrade.PREMIUM, AllocationQualityGrade.PROCESSING),
    (AllocationQualityGrade.STANDARD, AllocationQualityGrade.PREMIUM),
    (AllocationQualityGrade.STANDARD, AllocationQualityGrade.PROCESSING),
    (AllocationQualityGrade.PROCESSING, AllocationQualityGrade.PREMIUM),
    (AllocationQualityGrade.PROCESSING, AllocationQualityGrade.STANDARD),
    (AllocationQualityGrade.REJECTED, AllocationQualityGrade.STANDARD),
])
def test_every_remaining_disallowed_quality_combination(batch_grade, order_grade):
    result = calculate_advisory_draft(order(allocation_required_quality_grade=order_grade), [batch(allocation_quality_grade=batch_grade)])
    assert "QUALITY_INCOMPATIBLE" in {x.code for x in result.batch_exclusions[UUID(int=1)]}


def test_legacy_quality_mismatch_does_not_block_controlled_premium_to_standard():
    result = calculate_advisory_draft(
        order(allocation_required_quality_grade=AllocationQualityGrade.STANDARD, required_quality_grade="Export Quality"),
        [batch(allocation_quality_grade=AllocationQualityGrade.PREMIUM, quality_grade="Grade A")],
    )
    assert result.lines and result.advisory
