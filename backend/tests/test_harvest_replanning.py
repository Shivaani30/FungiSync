from decimal import Decimal

import pytest

from app.harvest_replanning import (
    SCENARIO_ID,
    build_synthetic_harvest_replan,
    reconcile_synthetic_quantities,
)


def test_synthetic_harvest_replan_reconciles_without_creating_commitments() -> None:
    result = build_synthetic_harvest_replan()

    assert result.scenario_id == SCENARIO_ID
    assert result.is_synthetic is True
    assert result.source_supply_kg == Decimal("60")
    assert result.preserved_commitments[0].buyer_label == "Buyer A"
    assert result.preserved_commitments[0].quantity_kg == Decimal("40")
    assert result.preserved_commitments[0].unchanged_input_fact is True
    assert result.alternate_proposal.buyer_label == "Alternate buyer"
    assert result.alternate_proposal.quantity_kg == Decimal("15")
    assert result.alternate_proposal.status == "PROPOSED"
    assert result.unassigned_kg == Decimal("5")
    assert result.unassigned_basis == "synthetic_reconciliation_remainder"
    assert result.reconciled_total_kg == Decimal("60")
    assert result.is_reconciled is True
    assert result.approval_status == "PENDING"
    assert result.reservation_committed is False
    assert result.buyer_commitment_created is False


def test_cancellation_is_a_scenario_event_not_an_order_or_reservation_mutation() -> None:
    cancellation = build_synthetic_harvest_replan().simulated_cancellation

    assert cancellation.buyer_label == "Buyer B"
    assert cancellation.quantity_kg == Decimal("20")
    assert cancellation.event_type == "simulated_order_cancellation"
    assert cancellation.actual_order_mutated is False
    assert cancellation.reservation_state_changed is False


def test_scenario_quantities_are_decimal_values() -> None:
    result = build_synthetic_harvest_replan()
    quantities = (
        result.source_supply_kg,
        result.simulated_cancellation.quantity_kg,
        *(item.quantity_kg for item in result.preserved_commitments),
        result.alternate_proposal.quantity_kg,
        result.unassigned_kg,
        result.reconciled_total_kg,
    )

    assert all(isinstance(quantity, Decimal) for quantity in quantities)


def test_reconciliation_rejects_inconsistent_quantities() -> None:
    with pytest.raises(ValueError, match="reconcile exactly"):
        reconcile_synthetic_quantities(
            source_supply_kg=Decimal("60"),
            preserved_commitment_kg=Decimal("40"),
            alternate_proposal_kg=Decimal("14"),
            unassigned_kg=Decimal("5"),
        )


@pytest.mark.parametrize("invalid", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), 1.0])
def test_reconciliation_rejects_invalid_decimal_inputs(invalid: object) -> None:
    with pytest.raises(ValueError, match="finite non-negative Decimal"):
        reconcile_synthetic_quantities(
            source_supply_kg=Decimal("60"),
            preserved_commitment_kg=Decimal("40"),
            alternate_proposal_kg=Decimal("15"),
            unassigned_kg=invalid,  # type: ignore[arg-type]
        )
