from datetime import date
from uuid import uuid4

def grower(client):
    response = client.post("/growers", json={"name":"Asha","phone":"9999999999","location":"Pune"})
    assert response.status_code == 201
    return response.json()
def buyer(client):
    response = client.post("/buyers", json={"name":"Market","location":"Mumbai","contact":"market@example.com"})
    assert response.status_code == 201
    return response.json()

def test_grower_crud_and_validation(client):
    item = grower(client)
    assert client.get("/growers").status_code == 200
    assert client.get(f"/growers/{item['id']}").json()["name"] == "Asha"
    assert client.patch(f"/growers/{item['id']}", json={"location":"Nashik"}).status_code == 200
    assert client.delete(f"/growers/{item['id']}").status_code == 204
    assert client.get(f"/growers/{item['id']}").status_code == 404
    assert client.post("/growers", json={"name":"", "phone":"1","location":"x"}).status_code == 422
    assert client.get("/growers/not-a-uuid").status_code == 422

def test_batch_crud_filters_foreign_key_and_parent_restriction(client):
    parent = grower(client)
    body = {"grower_id":parent["id"],"species":"oyster","expected_kg":"12.500","harvest_date":str(date.today())}
    created = client.post("/batches", json=body); assert created.status_code == 201
    item = created.json()
    assert len(client.get("/batches", params={"grower_id":parent["id"]}).json()) == 1
    assert client.patch(f"/batches/{item['id']}", json={"confirmed_kg":"10.000","status":"confirmed"}).status_code == 200
    assert client.post("/batches", json={**body,"grower_id":str(uuid4())}).status_code == 404
    assert client.post("/batches", json={**body,"expected_kg":"-1"}).status_code == 422
    assert client.delete(f"/growers/{parent['id']}").status_code == 409
    assert client.delete(f"/batches/{item['id']}").status_code == 204

def test_buyer_crud_and_order_crud_filters_and_validation(client):
    parent = buyer(client)
    assert client.patch(f"/buyers/{parent['id']}", json={"contact":"updated@example.com"}).status_code == 200
    body = {"buyer_id":parent["id"],"species":"shiitake","quantity_kg":"2.500","price_limit":"300.00","delivery_date":str(date.today())}
    created = client.post("/orders", json=body); assert created.status_code == 201
    item = created.json()
    assert len(client.get("/orders", params={"buyer_id":parent["id"],"species":"shiitake"}).json()) == 1
    assert client.get(f"/orders/{item['id']}").status_code == 200
    assert client.patch(f"/orders/{item['id']}", json={"price_limit":"350.00"}).status_code == 200
    assert client.post("/orders", json={**body,"buyer_id":str(uuid4())}).status_code == 404
    assert client.post("/orders", json={**body,"quantity_kg":"-1"}).status_code == 422
    assert client.post("/orders", json={**body,"price_limit":"-1"}).status_code == 422
    assert client.delete(f"/buyers/{parent['id']}").status_code == 409
    assert client.delete(f"/orders/{item['id']}").status_code == 204
    assert client.delete(f"/buyers/{parent['id']}").status_code == 204

def test_missing_records_and_required_fields(client):
    missing = str(uuid4())
    assert client.get(f"/buyers/{missing}").status_code == 404
    assert client.patch(f"/orders/{missing}", json={"status":"cancelled"}).status_code == 404
    assert client.delete(f"/batches/{missing}").status_code == 404
    assert client.post("/buyers", json={"name":"x"}).status_code == 422

def test_matching_preparation_fields_and_backward_compatibility(client):
    parent_grower = grower(client)
    batch_body = {
        "grower_id": parent_grower["id"], "species": "oyster",
        "expected_kg": "5.000", "harvest_date": "2026-11-10",
        "asking_price_per_kg": "125.50", "quality_grade": " premium ",
        "best_before_date": "2026-11-12",
    }
    created_batch = client.post("/batches", json=batch_body)
    assert created_batch.status_code == 201
    assert created_batch.json()["asking_price_per_kg"] == "125.50"
    assert created_batch.json()["quality_grade"] == "premium"
    assert created_batch.json()["best_before_date"] == "2026-11-12"
    assert client.post("/batches", json={**batch_body, "asking_price_per_kg": "-0.01"}).status_code == 422
    assert client.post("/batches", json={**batch_body, "best_before_date": "2026-11-09"}).status_code == 422
    assert client.post("/batches", json={**batch_body, "quality_grade": "   "}).status_code == 422

    parent_buyer = buyer(client)
    order_body = {
        "buyer_id": parent_buyer["id"], "species": "oyster",
        "quantity_kg": "5.000", "price_limit": "150.00",
        "delivery_date": "2026-11-11", "required_quality_grade": "premium",
    }
    created_order = client.post("/orders", json=order_body)
    assert created_order.status_code == 201
    assert created_order.json()["required_quality_grade"] == "premium"
    assert client.post("/orders", json={**order_body, "required_quality_grade": " "}).status_code == 422

def test_matching_endpoints_return_explanations_and_do_not_mutate_data(client):
    parent_grower = grower(client)
    parent_buyer = buyer(client)
    batch = client.post("/batches", json={
        "grower_id": parent_grower["id"], "species": "oyster", "expected_kg": "10",
        "confirmed_kg": "8", "harvest_date": "2026-11-10", "best_before_date": "2026-11-14",
        "asking_price_per_kg": "90", "quality_grade": "premium", "status": "confirmed",
    }).json()
    order = client.post("/orders", json={
        "buyer_id": parent_buyer["id"], "species": "oyster", "quantity_kg": "10",
        "price_limit": "100", "delivery_date": "2026-11-12", "required_quality_grade": "premium",
    }).json()
    response = client.get(f"/matches/orders/{order['id']}")
    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["batch_id"] == batch["id"]
    assert candidate["eligible"] is True
    assert candidate["criteria"]["quantity"]["status"] == "partial"
    assert candidate["quantity_that_could_be_supplied_kg"] == "8.000"
    assert candidate["compatibility_score"] == "84.00"
    inverse = client.get(f"/matches/batches/{batch['id']}")
    assert inverse.status_code == 200
    assert inverse.json()["candidates"][0]["order_id"] == order["id"]
    persisted = client.get(f"/batches/{batch['id']}").json()
    assert persisted["confirmed_kg"] == "8.000"

def test_matching_filters_missing_records_and_excluded_candidates(client):
    parent_grower = grower(client)
    parent_buyer = buyer(client)
    batch = client.post("/batches", json={
        "grower_id": parent_grower["id"], "species": "shiitake", "expected_kg": "3",
        "confirmed_kg": "3", "harvest_date": "2026-11-10", "status": "confirmed",
    }).json()
    order = client.post("/orders", json={
        "buyer_id": parent_buyer["id"], "species": "oyster", "quantity_kg": "2",
        "price_limit": "100", "delivery_date": "2026-11-12",
    }).json()
    excluded = client.get(f"/matches/orders/{order['id']}", params={"eligible": "false"})
    assert excluded.status_code == 200
    assert excluded.json()["candidates"][0]["batch_id"] == batch["id"]
    assert excluded.json()["candidates"][0]["exclusion_reasons"]
    assert client.get(f"/matches/orders/{uuid4()}").status_code == 404
    assert client.get(f"/matches/batches/{uuid4()}").status_code == 404
    assert client.get("/matches/orders/not-a-uuid").status_code == 422
    assert client.get(f"/matches/orders/{order['id']}", params={"minimum_score": "101"}).status_code == 422
    assert client.get(f"/matches/orders/{order['id']}", params={"limit": "0"}).status_code == 422
    assert client.get(f"/matches/orders/{order['id']}", params={"species": "oyster"}).json()["candidates"] == []
