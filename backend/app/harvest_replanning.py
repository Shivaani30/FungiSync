"""Pure synthetic harvest-replanning scenario for hackathon demonstrations.

This fixture is intentionally separate from production allocation and
reservation services. Its quantities are illustrative and do not describe
confirmed stock, actual orders, or real buyer commitments.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


ZERO = Decimal("0")
SCENARIO_ID = "synthetic-cancellation-replan-60kg-v1"


@dataclass(frozen=True)
class SimulatedCancellation:
    buyer_label: str
    quantity_kg: Decimal
    event_type: Literal["simulated_order_cancellation"]
    actual_order_mutated: bool
    reservation_state_changed: bool


@dataclass(frozen=True)
class PreservedCommitment:
    buyer_label: str
    quantity_kg: Decimal
    unchanged_input_fact: bool


@dataclass(frozen=True)
class AlternateBuyerProposal:
    buyer_label: str
    quantity_kg: Decimal
    status: Literal["PROPOSED"]


@dataclass(frozen=True)
class SyntheticHarvestReplan:
    scenario_id: str
    is_synthetic: bool
    source_supply_kg: Decimal
    simulated_cancellation: SimulatedCancellation
    preserved_commitments: tuple[PreservedCommitment, ...]
    alternate_proposal: AlternateBuyerProposal
    unassigned_kg: Decimal
    unassigned_basis: Literal["synthetic_reconciliation_remainder"]
    reconciled_total_kg: Decimal
    is_reconciled: bool
    approval_status: Literal["PENDING"]
    reservation_committed: bool
    buyer_commitment_created: bool


def _require_nonnegative_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
    return value


def reconcile_synthetic_quantities(
    *,
    source_supply_kg: Decimal,
    preserved_commitment_kg: Decimal,
    alternate_proposal_kg: Decimal,
    unassigned_kg: Decimal,
) -> Decimal:
    """Validate and return the exact total represented by the scenario parts."""
    source_supply = _require_nonnegative_decimal(source_supply_kg, "source_supply_kg")
    preserved = _require_nonnegative_decimal(preserved_commitment_kg, "preserved_commitment_kg")
    proposed = _require_nonnegative_decimal(alternate_proposal_kg, "alternate_proposal_kg")
    unassigned = _require_nonnegative_decimal(unassigned_kg, "unassigned_kg")

    reconciled_total = preserved + proposed + unassigned
    if reconciled_total != source_supply:
        raise ValueError("synthetic quantities must reconcile exactly to source_supply_kg")
    return reconciled_total


def build_synthetic_harvest_replan() -> SyntheticHarvestReplan:
    """Return the fixed 60 = 40 + 15 + 5 illustrative scenario."""
    source_supply = Decimal("60")
    retained = Decimal("40")
    proposed = Decimal("15")
    unassigned = Decimal("5")
    reconciled_total = reconcile_synthetic_quantities(
        source_supply_kg=source_supply,
        preserved_commitment_kg=retained,
        alternate_proposal_kg=proposed,
        unassigned_kg=unassigned,
    )

    return SyntheticHarvestReplan(
        scenario_id=SCENARIO_ID,
        is_synthetic=True,
        source_supply_kg=source_supply,
        simulated_cancellation=SimulatedCancellation(
            buyer_label="Buyer B",
            quantity_kg=Decimal("20"),
            event_type="simulated_order_cancellation",
            actual_order_mutated=False,
            reservation_state_changed=False,
        ),
        preserved_commitments=(
            PreservedCommitment(
                buyer_label="Buyer A",
                quantity_kg=retained,
                unchanged_input_fact=True,
            ),
        ),
        alternate_proposal=AlternateBuyerProposal(
            buyer_label="Alternate buyer",
            quantity_kg=proposed,
            status="PROPOSED",
        ),
        unassigned_kg=unassigned,
        unassigned_basis="synthetic_reconciliation_remainder",
        reconciled_total_kg=reconciled_total,
        is_reconciled=True,
        approval_status="PENDING",
        reservation_committed=False,
        buyer_commitment_created=False,
    )
