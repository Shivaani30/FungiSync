from datetime import date


def _grower(client, name, location="Pune"):
    response = client.post("/growers", json={"name": name, "phone": "9000000000", "location": location})
    assert response.status_code == 201
    return response.json()


def _buyer(client, name, location="Pune"):
    response = client.post("/buyers", json={"name": name, "location": location, "contact": name + "@example.com"})
    assert response.status_code == 201
    return response.json()


def _batch(client, grower_id, species, quantity, **extra):
    body = {
        "grower_id": grower_id, "species": species, "expected_kg": str(quantity),
        "confirmed_kg": str(quantity), "harvest_date": str(date(2026, 11, 10)),
        "status": "confirmed",
    }
    body.update(extra)
    response = client.post("/batches", json=body)
    assert response.status_code == 201
    return response.json()


def test_matching_api_ranks_and_explains_isolated_dataset(client):
    grower = _grower(client, "Exact Grower")
    second_grower = _grower(client, "Other Grower")
    buyer = _buyer(client, "Matching Buyer")
    order = client.post("/orders", json={
        "buyer_id": buyer["id"], "species": "oyster", "quantity_kg": "10",
        "price_limit": "100", "delivery_date": "2026-11-12",
        "required_quality_grade": "premium", "status": "pending",
    }).json()
    exact = _batch(client, grower["id"], "oyster", "10", asking_price_per_kg="90", quality_grade="premium", best_before_date="2026-11-14")
    partial = _batch(client, grower["id"], "oyster", "4", asking_price_per_kg="90", quality_grade="premium", best_before_date="2026-11-14")
    price_incompatible = _batch(client, grower["id"], "oyster", "10", asking_price_per_kg="101", quality_grade="premium", best_before_date="2026-11-14")
    species_incompatible = _batch(client, second_grower["id"], "shiitake", "10", asking_price_per_kg="90", quality_grade="premium", best_before_date="2026-11-14")
    missing_data = _batch(client, grower["id"], "oyster", "5")

    response = client.get("/matches/orders/" + order["id"])
    assert response.status_code == 200
    candidates = {candidate["batch_id"]: candidate for candidate in response.json()["candidates"]}
    assert candidates[exact["id"]]["eligible"] is True
    assert candidates[exact["id"]]["compatibility_score"] == "100.00"
    assert candidates[partial["id"]]["eligible"] is True
    assert candidates[partial["id"]]["quantity_that_could_be_supplied_kg"] == "4.000"
    assert candidates[partial["id"]]["remaining_quantity_kg"] == "6.000"
    assert candidates[price_incompatible["id"]]["eligible"] is False
    assert "price exceeds" in candidates[price_incompatible["id"]]["exclusion_reasons"][0]
    assert candidates[species_incompatible["id"]]["eligible"] is False
    assert "Species mismatch" in candidates[species_incompatible["id"]]["exclusion_reasons"][0]
    assert candidates[missing_data["id"]]["eligible"] is True
    assert candidates[missing_data["id"]]["data_completeness_percentage"] == "40.00"
    assert len(candidates[missing_data["id"]]["unknown_criteria"]) == 3
    assert client.get("/batches/" + exact["id"]).json()["confirmed_kg"] == "10.000"
    assert client.get("/orders/" + order["id"]).json()["quantity_kg"] == "10.000"
