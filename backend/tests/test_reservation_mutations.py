"""PostgreSQL coverage for reservation-aware batch and order PATCH operations."""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    AllocationLine, AllocationProposal,
    AllocationProposalVersion, AllocationQualityGrade, AllocationReservation,
    AllocationReservationStatus, Batch, BatchStatus, Buyer, Grower, Order,
    OrderStatus,
)
from app.reservation_mutation_service import (
    MutationTargetsChangedError, ReservationMutationConflictError, update_batch_reservation_aware,
    update_order_reservation_aware,
)
from app.reservation_service import (
    IneligibleReferencedRecordError, approve_allocation_proposal,
    submit_allocation_proposal,
)


def _factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed(engine, statuses=(AllocationReservationStatus.PENDING,), *, expired=False):
    with Session(engine) as session:
        grower = Grower(name="Mutation grower", phone="9999999999", location="Pune")
        buyer = Buyer(name="Mutation buyer", contact="mutation@example.invalid", location="Pune")
        session.add_all((grower, buyer))
        session.flush()
        batch = Batch(
            grower_id=grower.id, species="oyster", expected_kg=Decimal("10.000"),
            confirmed_kg=Decimal("10.000"), harvest_date=date(2026, 11, 10),
            best_before_date=date(2026, 11, 12), asking_price_per_kg=Decimal("100.00"),
            allocation_quality_grade=AllocationQualityGrade.PREMIUM,
            status=BatchStatus.CONFIRMED,
        )
        order = Order(
            buyer_id=buyer.id, species="oyster", quantity_kg=Decimal("10.000"),
            price_limit=Decimal("100.00"), delivery_date=date(2026, 11, 11),
            allocation_required_quality_grade=AllocationQualityGrade.PREMIUM,
            status=OrderStatus.PENDING,
        )
        proposal = AllocationProposal()
        session.add_all((batch, order, proposal))
        session.flush()
        version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
        session.add(version)
        session.flush()
        for index, status in enumerate(statuses):
            quantity = (Decimal("3.500"), Decimal("2.250"))[index]
            line = AllocationLine(
                proposal_version_id=version.id, batch_id=batch.id,
                order_id=order.id, proposed_quantity_kg=quantity,
            )
            session.add(line)
            session.flush()
            if status is None:
                continue
            session.add(AllocationReservation(
                allocation_line_id=line.id, proposal_id=proposal.id,
                proposal_version_id=version.id, reserved_quantity_kg=quantity,
                status=status,
                expires_at=datetime(2000 if expired else 2099, 1, 1, tzinfo=timezone.utc)
                if status is AllocationReservationStatus.PENDING else None,
                committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
                if status in {AllocationReservationStatus.COMMITTED, AllocationReservationStatus.RELEASED} else None,
                terminal_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
                if status in {
                    AllocationReservationStatus.REJECTED, AllocationReservationStatus.CANCELLED,
                    AllocationReservationStatus.EXPIRED, AllocationReservationStatus.RELEASED,
                } else None,
            ))
        session.commit()
        return batch.id, order.id, proposal.id


@pytest.mark.parametrize("kind", ["batch", "order"])
@pytest.mark.parametrize("status", [AllocationReservationStatus.PENDING, AllocationReservationStatus.COMMITTED])
def test_quantity_patch_uses_decimal_aggregate_and_rejects_below_active(test_engine, client, kind, status):
    batch_id, order_id, _ = _seed(test_engine, (status, status))
    item_id, field, path = (
        (batch_id, "confirmed_kg", "batches") if kind == "batch"
        else (order_id, "quantity_kg", "orders")
    )
    url = f"/{path}/{item_id}"
    assert client.patch(url, json={field: "12.000"}).status_code == 200
    assert client.patch(url, json={field: "5.750"}).status_code == 200
    rejected = client.patch(url, json={field: "5.749"})
    assert rejected.status_code == 409
    assert Decimal(client.get(url).json()[field]) == Decimal("5.750")


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_expired_pending_does_not_consume_capacity_but_blocks_eligibility(test_engine, client, kind):
    batch_id, order_id, _ = _seed(test_engine, expired=True)
    item_id, field, path = (
        (batch_id, "confirmed_kg", "batches") if kind == "batch"
        else (order_id, "quantity_kg", "orders")
    )
    url = f"/{path}/{item_id}"
    assert client.patch(url, json={field: "0.000"}).status_code == 200
    assert client.patch(url, json={"species": "shiitake"}).status_code == 409
    assert client.get(url).json()["species"] == "oyster"


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_eligibility_fields_noops_unrelated_fields_and_atomic_mixed_patch(test_engine, client, kind):
    batch_id, order_id, _ = _seed(test_engine)
    item_id, path = (batch_id, "batches") if kind == "batch" else (order_id, "orders")
    url = f"/{path}/{item_id}"
    changed = (
        {"status": "harvested", "species": "shiitake", "harvest_date": "2026-11-09",
         "asking_price_per_kg": "99.00", "best_before_date": "2026-11-13"}
        if kind == "batch" else
        {"status": "confirmed", "species": "shiitake", "price_limit": "99.00",
         "delivery_date": "2026-11-12"}
    )
    initial = client.get(url).json()
    for field, value in changed.items():
        assert client.patch(url, json={field: value}).status_code == 409, field
        assert client.get(url).json()[field] == initial[field]
    assert client.patch(url, json={"species": "oyster"}).status_code == 200
    unrelated = {"expected_kg": "11.000"} if kind == "batch" else {"buyer_id": initial["buyer_id"]}
    assert client.patch(url, json=unrelated).status_code == 200
    mixed = {**unrelated, "species": "shiitake"}
    if kind == "batch":
        mixed["expected_kg"] = "12.000"
    assert client.patch(url, json=mixed).status_code == 409
    stored = client.get(url).json()
    assert stored["species"] == "oyster"
    if kind == "batch":
        assert stored["expected_kg"] == "11.000"
    assert client.patch(url, json={"species": ""}).status_code == 422


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_controlled_grade_is_protected_in_service(test_engine, kind):
    batch_id, order_id, _ = _seed(test_engine)
    operation = update_batch_reservation_aware if kind == "batch" else update_order_reservation_aware
    item_id = batch_id if kind == "batch" else order_id
    field = "allocation_quality_grade" if kind == "batch" else "allocation_required_quality_grade"
    with pytest.raises(ReservationMutationConflictError):
        operation(_factory(test_engine), item_id, {field: AllocationQualityGrade.STANDARD})
    assert operation(_factory(test_engine), item_id, {field: AllocationQualityGrade.PREMIUM}).id == item_id


@pytest.mark.parametrize("kind", ["batch", "order"])
@pytest.mark.parametrize("status", [
    AllocationReservationStatus.REJECTED, AllocationReservationStatus.CANCELLED,
    AllocationReservationStatus.EXPIRED, AllocationReservationStatus.RELEASED,
])
def test_terminal_reservations_do_not_block_eligibility(test_engine, client, kind, status):
    batch_id, order_id, _ = _seed(test_engine, (status,))
    item_id, path = (batch_id, "batches") if kind == "batch" else (order_id, "orders")
    assert client.patch(f"/{path}/{item_id}", json={"species": "shiitake"}).status_code == 200


def test_omitted_fields_and_explicit_null_keep_existing_patch_semantics(test_engine, client):
    batch_id, order_id, _ = _seed(test_engine, ())
    batch_url = f"/batches/{batch_id}"
    order_url = f"/orders/{order_id}"
    original_batch = client.get(batch_url).json()
    original_order = client.get(order_url).json()
    assert client.patch(batch_url, json={"best_before_date": None}).status_code == 200
    stored_batch = client.get(batch_url).json()
    assert stored_batch["best_before_date"] is None
    assert stored_batch["harvest_date"] == original_batch["harvest_date"]
    assert stored_batch["confirmed_kg"] == original_batch["confirmed_kg"]
    assert client.patch(order_url, json={"quantity_kg": None}).status_code == 409
    stored_order = client.get(order_url).json()
    assert stored_order["quantity_kg"] == original_order["quantity_kg"]
    assert stored_order["price_limit"] == original_order["price_limit"]

    active_batch_id, active_order_id, _ = _seed(test_engine)
    assert client.patch(f"/batches/{active_batch_id}", json={"confirmed_kg": None}).status_code == 409
    assert client.patch(f"/orders/{active_order_id}", json={"quantity_kg": None}).status_code == 409
    assert client.get(f"/batches/{active_batch_id}").json()["confirmed_kg"] == "10.000"
    assert client.get(f"/orders/{active_order_id}").json()["quantity_kg"] == "10.000"


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_mutation_uses_global_lock_category_order(test_engine, kind):
    batch_id, order_id, _ = _seed(test_engine)
    target_id = batch_id if kind == "batch" else order_id
    operation = update_batch_reservation_aware if kind == "batch" else update_order_reservation_aware
    categories = []
    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "FOR UPDATE" in statement:
            for table in (
                "batches", "orders", "allocation_proposals",
                "allocation_lines", "allocation_reservations",
            ):
                if f"FROM {table}" in statement:
                    categories.append(table)
                    break
    event.listen(test_engine, "before_cursor_execute", observe)
    try:
        operation(_factory(test_engine), target_id, {"species": "oyster"})
    finally:
        event.remove(test_engine, "before_cursor_execute", observe)
    assert categories == [
        "batches", "orders", "allocation_proposals",
        "allocation_lines", "allocation_reservations",
    ]


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_line_set_change_between_discovery_and_parent_lock_aborts(test_engine, kind):
    batch_id, order_id, _ = _seed(test_engine, (None,))
    target_id = batch_id if kind == "batch" else order_id
    update = update_batch_reservation_aware if kind == "batch" else update_order_reservation_aware
    entered = threading.Event()
    release = threading.Event()
    worker_id = []
    errors = []

    def pause_before_parent_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
        if (
            worker_id and threading.get_ident() == worker_id[0]
            and "FROM batches" in statement and "FOR UPDATE" in statement
            and not entered.is_set()
        ):
            entered.set()
            assert release.wait(10)

    def patch():
        worker_id.append(threading.get_ident())
        try:
            update(_factory(test_engine), target_id, {"species": "shiitake"})
        except BaseException as error:
            errors.append(error)

    event.listen(test_engine, "before_cursor_execute", pause_before_parent_lock)
    worker = threading.Thread(target=patch)
    worker.start()
    try:
        assert entered.wait(10)
        with Session(test_engine) as session:
            proposal = AllocationProposal()
            session.add(proposal)
            session.flush()
            version = AllocationProposalVersion(proposal_id=proposal.id, version_number=1)
            session.add(version)
            session.flush()
            session.add(AllocationLine(
                proposal_version_id=version.id, batch_id=batch_id,
                order_id=order_id, proposed_quantity_kg=Decimal("1.000"),
            ))
            session.commit()
    finally:
        release.set()
        worker.join(10)
        event.remove(test_engine, "before_cursor_execute", pause_before_parent_lock)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MutationTargetsChangedError)
    with Session(test_engine) as session:
        target = session.get(Batch if kind == "batch" else Order, target_id)
        assert target is not None and target.species == "oyster"


@pytest.mark.parametrize("kind", ["batch", "order"])
def test_patch_serializes_with_submission_and_approval(test_engine, kind):
    # The lifecycle operation waits for a target parent lock held by PATCH.
    # Both services then observe the committed outcome through their own locks.
    for approve in (False, True):
        batch_id, order_id, proposal_id = _seed(test_engine, (None,))
        factory = _factory(test_engine)
        if approve:
            submit_allocation_proposal(factory, proposal_id)
        target_id = batch_id if kind == "batch" else order_id
        update = update_batch_reservation_aware if kind == "batch" else update_order_reservation_aware
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def hold_parent_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
            if "FROM batches" in statement and "FOR UPDATE" in statement and kind == "batch":
                entered.set()
                assert release.wait(10)
            elif "FROM orders" in statement and "FOR UPDATE" in statement and kind == "order":
                entered.set()
                assert release.wait(10)

        # Listener is restricted to the PATCH worker's connection through its
        # thread ID; lifecycle SQL must not block inside the listener.
        worker_id = []
        lifecycle_id = []
        lifecycle_lock_attempt = threading.Event()
        def after_patch_lock(connection, cursor, statement, parameters, context, executemany):
            if worker_id and threading.get_ident() == worker_id[0]:
                hold_parent_lock(connection, cursor, statement, parameters, context, executemany)

        def before_lifecycle_lock(connection, cursor, statement, parameters, context, executemany):
            if lifecycle_id and threading.get_ident() == lifecycle_id[0]:
                if "FOR UPDATE" in statement and "FROM batches" in statement:
                    lifecycle_lock_attempt.set()

        def patch():
            worker_id.append(threading.get_ident())
            try:
                update(factory, target_id, {"species": "shiitake"})
            except BaseException as error:
                errors.append(error)

        event.listen(test_engine, "after_cursor_execute", after_patch_lock)
        event.listen(test_engine, "before_cursor_execute", before_lifecycle_lock)
        patch_thread = threading.Thread(target=patch)
        patch_thread.start()
        try:
            assert entered.wait(10)
            lifecycle_errors = []
            def lifecycle():
                lifecycle_id.append(threading.get_ident())
                try:
                    (approve_allocation_proposal if approve else submit_allocation_proposal)(factory, proposal_id)
                except BaseException as error:
                    lifecycle_errors.append(error)
            lifecycle_thread = threading.Thread(target=lifecycle)
            lifecycle_thread.start()
            assert lifecycle_lock_attempt.wait(10)
            release.set()
            patch_thread.join(10)
            lifecycle_thread.join(10)
            assert not patch_thread.is_alive() and not lifecycle_thread.is_alive()
            if approve:
                assert len(errors) == 1 and isinstance(errors[0], ReservationMutationConflictError)
                assert not lifecycle_errors
            else:
                assert not errors
                assert len(lifecycle_errors) == 1 and isinstance(lifecycle_errors[0], IneligibleReferencedRecordError)
        finally:
            release.set()
            event.remove(test_engine, "after_cursor_execute", after_patch_lock)
            event.remove(test_engine, "before_cursor_execute", before_lifecycle_lock)
