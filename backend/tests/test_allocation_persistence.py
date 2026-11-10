from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.allocation_engine import AdvisoryAllocationLine, AdvisoryDraftResult, calculate_advisory_draft
from app.allocation_persistence import persist_advisory_draft
from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationProposal,
    AllocationProposalStatus,
    AllocationProposalVersion,
    AllocationQualityGrade,
    Batch,
    BatchStatus,
    Buyer,
    Grower,
    Order,
    OrderStatus,
)


TODAY = date(2026, 11, 10)


@pytest.fixture()
def factory(test_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def _seed(
    factory: sessionmaker[Session],
    *,
    batch_quantities: tuple[Decimal, ...] = (Decimal("5.000"),),
    order_quantity: Decimal = Decimal("5.000"),
    order_status: OrderStatus = OrderStatus.PENDING,
    batch_status: BatchStatus = BatchStatus.CONFIRMED,
) -> tuple[UUID, tuple[UUID, ...]]:
    with factory.begin() as db:
        grower = Grower(name="Persistence grower", phone="9999999999", location="Pune")
        buyer = Buyer(name="Persistence buyer", location="Pune", contact="buyer@example.invalid")
        db.add_all([grower, buyer])
        db.flush()
        order = Order(
            buyer_id=buyer.id,
            species="oyster",
            quantity_kg=order_quantity,
            price_limit=Decimal("100.00"),
            delivery_date=TODAY + timedelta(days=2),
            required_quality_grade="legacy",
            allocation_required_quality_grade=AllocationQualityGrade.PREMIUM,
            status=order_status,
        )
        batches = [
            Batch(
                grower_id=grower.id,
                species="oyster",
                expected_kg=quantity,
                confirmed_kg=quantity,
                harvest_date=TODAY,
                best_before_date=TODAY + timedelta(days=4),
                asking_price_per_kg=Decimal("90.00"),
                quality_grade="legacy",
                allocation_quality_grade=AllocationQualityGrade.PREMIUM,
                status=batch_status,
            )
            for quantity in batch_quantities
        ]
        db.add_all([order, *batches])
        db.flush()
        return order.id, tuple(batch.id for batch in batches)


def _draft(factory: sessionmaker[Session], order_id: UUID, batch_ids: tuple[UUID, ...]) -> AdvisoryDraftResult:
    with factory() as db:
        order = db.get(Order, order_id)
        assert order is not None
        batches = [db.get(Batch, batch_id) for batch_id in batch_ids]
        assert all(batch is not None for batch in batches)
        return calculate_advisory_draft(order, batches)


def _manual_result(
    order_id: UUID,
    lines: tuple[AdvisoryAllocationLine, ...],
    *,
    total: Decimal,
    unfulfilled: Decimal,
    advisory: bool = True,
    batch_exclusions: dict[UUID, tuple] | None = None,
) -> AdvisoryDraftResult:
    return AdvisoryDraftResult(
        advisory=advisory,
        order_id=order_id,
        lines=lines,
        batch_exclusions=batch_exclusions or {},
        order_exclusions=(),
        warnings=("advisory warning",),
        total_proposed_kg=total,
        unfulfilled_kg=unfulfilled,
    )


def _allocation_count(factory: sessionmaker[Session], model: type) -> int:
    with factory() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_one_line_persistence_relationships_and_json_metadata(factory) -> None:
    order_id, batch_ids = _seed(factory)
    result = _draft(factory, order_id, batch_ids)
    stored = persist_advisory_draft(factory, result, ruleset_version="phase-3.2.2")

    with factory() as db:
        proposal = db.get(AllocationProposal, stored.proposal_id)
        version = db.get(AllocationProposalVersion, stored.proposal_version_id)
        line = db.get(AllocationLine, stored.line_ids[0])
        event = db.get(AllocationEvent, stored.event_id)
        assert proposal is not None and proposal.status is AllocationProposalStatus.DRAFT
        assert proposal.ruleset_version == "phase-3.2.2"
        assert version is not None and version.proposal_id == proposal.id and version.version_number == 1
        assert line is not None and line.proposal_version_id == version.id
        assert line.batch_id == batch_ids[0] and line.order_id == order_id
        assert event is not None and event.proposal_id == proposal.id and event.proposal_version_id == version.id
        assert event.event_type == "proposal_generated"
        assert event.details == {
            "advisory": True,
            "order_id": str(order_id),
            "ruleset_version": "phase-3.2.2",
            "lines": [{"batch_id": str(batch_ids[0]), "order_id": str(order_id), "proposed_quantity_kg": "5.000"}],
            "total_proposed_kg": "5.000",
            "unfulfilled_kg": "0.000",
            "warnings": ["Advisory draft only: no stock is reserved and availability may change before approval."],
            "order_exclusions": [],
            "batch_exclusions": {},
        }


def test_multi_batch_split_and_partial_fulfillment_preserve_source_quantities(factory) -> None:
    order_id, batch_ids = _seed(factory, batch_quantities=(Decimal("2.000"), Decimal("3.000")), order_quantity=Decimal("8.000"))
    result = _draft(factory, order_id, batch_ids)
    stored = persist_advisory_draft(factory, result, ruleset_version=None)
    assert len(stored.line_ids) == 2
    with factory() as db:
        quantities = list(db.scalars(select(AllocationLine.proposed_quantity_kg).order_by(AllocationLine.proposed_quantity_kg)))
        event = db.get(AllocationEvent, stored.event_id)
        assert quantities == [Decimal("2.000"), Decimal("3.000")]
        assert event is not None and event.details["unfulfilled_kg"] == "3.000"
        assert [db.get(Batch, batch_id).confirmed_kg for batch_id in batch_ids] == [Decimal("2.000"), Decimal("3.000")]
        assert db.get(Order, order_id).quantity_kg == Decimal("8.000")


def test_zero_line_persistence_is_valid(factory) -> None:
    order_id, _ = _seed(factory, batch_quantities=(), order_quantity=Decimal("5.000"))
    result = _draft(factory, order_id, ())
    stored = persist_advisory_draft(factory, result, ruleset_version="phase-3.2.2")
    assert stored.line_ids == ()
    assert _allocation_count(factory, AllocationProposal) == 1
    assert _allocation_count(factory, AllocationLine) == 0
    with factory() as db:
        event = db.get(AllocationEvent, stored.event_id)
        assert event is not None and event.details["total_proposed_kg"] == "0" and event.details["unfulfilled_kg"] == "5.000"


@pytest.mark.parametrize(
    "change",
    [
        "missing_order",
        "missing_line_batch",
        "missing_exclusion_batch",
        "cancelled_order",
        "fulfilled_order",
        "cancelled_batch",
        "expected_batch",
    ],
)
def test_missing_or_ineligible_current_references_rollback(factory, change: str) -> None:
    order_id, batch_ids = _seed(factory)
    result = _draft(factory, order_id, batch_ids)
    if change == "missing_order":
        missing_order_id = uuid4()
        result = replace(result, order_id=missing_order_id, lines=tuple(replace(line, order_id=missing_order_id) for line in result.lines))
    elif change == "missing_line_batch":
        line = replace(result.lines[0], batch_id=uuid4())
        result = replace(result, lines=(line,))
    elif change == "missing_exclusion_batch":
        result = replace(result, batch_exclusions={uuid4(): ()})
    elif change in {"cancelled_order", "fulfilled_order"}:
        with factory.begin() as db:
            db.get(Order, order_id).status = OrderStatus.CANCELLED if change == "cancelled_order" else OrderStatus.FULFILLED
    elif change in {"cancelled_batch", "expected_batch"}:
        with factory.begin() as db:
            db.get(Batch, batch_ids[0]).status = BatchStatus.CANCELLED if change == "cancelled_batch" else BatchStatus.EXPECTED

    with pytest.raises(ValueError):
        persist_advisory_draft(factory, result, ruleset_version=None)
    assert _allocation_count(factory, AllocationProposal) == 0
    assert _allocation_count(factory, AllocationEvent) == 0


def test_duplicate_batch_mismatched_order_and_false_advisory_reject(factory) -> None:
    order_id, batch_ids = _seed(factory)
    result = _draft(factory, order_id, batch_ids)
    duplicate = replace(result.lines[0], quantity_kg=Decimal("1.000"))
    cases = [
        replace(result, lines=(result.lines[0], duplicate), total_proposed_kg=Decimal("6.000")),
        replace(result, lines=(replace(result.lines[0], order_id=uuid4()),)),
        replace(result, advisory=False),
    ]
    for invalid in cases:
        with pytest.raises(ValueError):
            persist_advisory_draft(factory, invalid, ruleset_version=None)
    assert _allocation_count(factory, AllocationProposal) == 0


def test_non_string_warning_or_exclusion_fields_reject(factory) -> None:
    order_id, batch_ids = _seed(factory)
    result = _draft(factory, order_id, batch_ids)
    invalid_warning = replace(result, warnings=("valid", 1))
    invalid_exclusion = replace(result, batch_exclusions={batch_ids[0]: (object(),)})
    for invalid in (invalid_warning, invalid_exclusion):
        with pytest.raises(ValueError):
            persist_advisory_draft(factory, invalid, ruleset_version=None)
    assert _allocation_count(factory, AllocationProposal) == 0


@pytest.mark.parametrize("quantity", [1.0, Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-0.001"), Decimal("1.0001"), Decimal("1000000000.000")])
def test_invalid_line_quantity_rejects_without_rounding(factory, quantity) -> None:
    order_id, batch_ids = _seed(factory, order_quantity=Decimal("5.000"))
    line = AdvisoryAllocationLine(batch_ids[0], order_id, quantity, Decimal("0"))
    result = _manual_result(order_id, (line,), total=Decimal("5.000"), unfulfilled=Decimal("0"))
    with pytest.raises(ValueError):
        persist_advisory_draft(factory, result, ruleset_version=None)
    assert _allocation_count(factory, AllocationProposal) == 0


def test_invalid_totals_and_reconciliation_reject(factory) -> None:
    order_id, batch_ids = _seed(factory)
    line = AdvisoryAllocationLine(batch_ids[0], order_id, Decimal("5.000"), Decimal("0"))
    for result in (
        _manual_result(order_id, (line,), total=Decimal("4.000"), unfulfilled=Decimal("1.000")),
        _manual_result(order_id, (line,), total=Decimal("5.000"), unfulfilled=Decimal("1.000")),
        _manual_result(order_id, (line,), total=Decimal("NaN"), unfulfilled=Decimal("0")),
    ):
        with pytest.raises(ValueError):
            persist_advisory_draft(factory, result, ruleset_version=None)
    assert _allocation_count(factory, AllocationProposal) == 0


def test_current_batch_quantity_boundary_and_existing_drafts_do_not_reserve(factory) -> None:
    order_id, batch_ids = _seed(factory)
    equal = _draft(factory, order_id, batch_ids)
    first = persist_advisory_draft(factory, equal, ruleset_version=None)
    second = persist_advisory_draft(factory, equal, ruleset_version=None)
    assert first.proposal_id != second.proposal_id
    with factory() as db:
        assert db.get(Batch, batch_ids[0]).confirmed_kg == Decimal("5.000")
        assert db.get(Order, order_id).quantity_kg == Decimal("5.000")

    with factory.begin() as db:
        db.get(Order, order_id).quantity_kg = Decimal("5.001")
    greater_line = AdvisoryAllocationLine(batch_ids[0], order_id, Decimal("5.001"), Decimal("0"))
    greater = _manual_result(order_id, (greater_line,), total=Decimal("5.001"), unfulfilled=Decimal("0"))
    with pytest.raises(ValueError, match="exceeds"):
        persist_advisory_draft(factory, greater, ruleset_version=None)


def test_forced_failure_rolls_back_and_separate_prior_read_is_safe(factory, monkeypatch) -> None:
    order_id, batch_ids = _seed(factory)
    result = _draft(factory, order_id, batch_ids)
    with factory() as request_like_session:
        assert request_like_session.get(Order, order_id) is not None

        import app.allocation_persistence as persistence

        def fail_event(**_):
            raise RuntimeError("forced event failure")

        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.setattr(persistence, "AllocationEvent", fail_event)
            with pytest.raises(RuntimeError, match="forced event failure"):
                persist_advisory_draft(factory, result, ruleset_version=None)

    assert _allocation_count(factory, AllocationProposal) == 0
    assert _allocation_count(factory, AllocationProposalVersion) == 0
    assert _allocation_count(factory, AllocationLine) == 0
    assert _allocation_count(factory, AllocationEvent) == 0

    persisted = persist_advisory_draft(factory, result, ruleset_version=None)
    assert persisted.proposal_id
