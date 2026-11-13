import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.api.routes.harvest_coordination import router
from app.schemas import SyntheticHarvestReplanOut


api_app = FastAPI(title="Standalone harvest coordination router test")
api_app.include_router(router)
client = TestClient(api_app)


def test_get_returns_typed_synthetic_replan_with_decimal_serialization() -> None:
    response = client.get("/harvest-coordination/demo/replan")

    assert response.status_code == 200
    payload = response.json()
    parsed = SyntheticHarvestReplanOut.model_validate(payload)

    assert payload["scenario_id"] == "synthetic-cancellation-replan-60kg-v1"
    assert payload["is_synthetic"] is True
    assert payload["source_supply_kg"] == "60"
    assert payload["simulated_cancellation"]["quantity_kg"] == "20"
    assert payload["simulated_cancellation"]["actual_order_mutated"] is False
    assert payload["simulated_cancellation"]["reservation_state_changed"] is False
    assert payload["preserved_commitments"] == [
        {"buyer_label": "Buyer A", "quantity_kg": "40", "unchanged_input_fact": True}
    ]
    assert payload["alternate_proposal"] == {
        "buyer_label": "Alternate buyer",
        "quantity_kg": "15",
        "status": "PROPOSED",
    }
    assert payload["unassigned_kg"] == "5"
    assert payload["unassigned_basis"] == "synthetic_reconciliation_remainder"
    assert payload["reconciled_total_kg"] == "60"
    assert payload["is_reconciled"] is True
    assert payload["approval_status"] == "PENDING"
    assert payload["reservation_committed"] is False
    assert payload["buyer_commitment_created"] is False
    assert parsed.source_supply_kg == parsed.reconciled_total_kg == 60
    assert (
        parsed.preserved_commitments[0].quantity_kg
        + parsed.alternate_proposal.quantity_kg
        + parsed.unassigned_kg
        == parsed.source_supply_kg
    )


def test_repeated_get_returns_identical_fixture() -> None:
    first = client.get("/harvest-coordination/demo/replan")
    second = client.get("/harvest-coordination/demo/replan")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_unsupported_query_parameters_are_rejected() -> None:
    response = client.get(
        "/harvest-coordination/demo/replan",
        params={"order_id": "any-value"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Query parameters are not supported"}


def test_openapi_documents_only_the_get_response_contract() -> None:
    operation = api_app.openapi()["paths"]["/harvest-coordination/demo/replan"]["get"]

    assert operation["summary"] == "Get the synthetic harvest replan demonstration"
    assert "fixed synthetic 60 kg scenario" in operation["description"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SyntheticHarvestReplanOut"
    }
    assert set(operation["responses"]) == {"200", "422"}
    assert "/harvest-coordination/demo/replan" in api_app.openapi()["paths"]


def test_router_is_database_independent_and_registers_one_get_route() -> None:
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert len(routes) == 1
    assert routes[0].path == "/harvest-coordination/demo/replan"
    assert routes[0].methods == {"GET"}
    assert routes[0].dependencies == []
    assert routes[0].response_model is SyntheticHarvestReplanOut
    assert "app.main" not in sys.modules
    assert "app.db.session" not in sys.modules
