import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Batch, Buyer, Grower, Order
from tests.db_safety import create_guarded_runtime_engine


engine = create_guarded_runtime_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class DatabaseSchemaTests(unittest.TestCase):
    """Integration tests against the PostgreSQL database configured in .env."""

    @staticmethod
    def grower() -> Grower:
        return Grower(
            name=f"FungiSync Stage 2 Grower {uuid4()}",
            phone="+910000000000",
            location="Test Farm",
        )

    @staticmethod
    def buyer() -> Buyer:
        return Buyer(
            name=f"FungiSync Stage 2 Buyer {uuid4()}",
            location="Test Market",
            contact="test@example.invalid",
        )

    @staticmethod
    def batch(grower_id) -> Batch:
        return Batch(
            grower_id=grower_id,
            species="oyster",
            expected_kg=Decimal("20.500"),
            confirmed_kg=Decimal("18.250"),
            harvest_date=date(2026, 11, 10),
        )

    @staticmethod
    def order(buyer_id) -> Order:
        return Order(
            buyer_id=buyer_id,
            species="oyster",
            quantity_kg=Decimal("12.500"),
            price_limit=Decimal("240.00"),
            delivery_date=date(2026, 11, 11),
        )

    def test_01_expected_tables_and_foreign_keys_exist(self) -> None:
        inspector = inspect(engine)
        self.assertTrue({"growers", "batches", "buyers", "orders"}.issubset(inspector.get_table_names()))

        batch_foreign_keys = inspector.get_foreign_keys("batches")
        order_foreign_keys = inspector.get_foreign_keys("orders")
        self.assertEqual(batch_foreign_keys[0]["referred_table"], "growers")
        self.assertEqual(order_foreign_keys[0]["referred_table"], "buyers")
        self.assertEqual(batch_foreign_keys[0]["options"]["ondelete"], "RESTRICT")
        self.assertEqual(order_foreign_keys[0]["options"]["ondelete"], "RESTRICT")

    def test_02_relationships_and_persistence_across_sessions(self) -> None:
        session = SessionLocal()
        grower_id = buyer_id = batch_id = order_id = None
        try:
            grower = self.grower()
            buyer = self.buyer()
            session.add_all([grower, buyer])
            session.flush()
            batch = self.batch(grower.id)
            order = self.order(buyer.id)
            session.add_all([batch, order])
            session.commit()
            grower_id, buyer_id = grower.id, buyer.id
            batch_id, order_id = batch.id, order.id

            session.close()
            engine.dispose()
            session = SessionLocal()

            stored_batch = session.get(Batch, batch_id)
            stored_order = session.get(Order, order_id)
            self.assertEqual(stored_batch.grower.id, grower_id)
            self.assertEqual(stored_order.buyer.id, buyer_id)
            self.assertEqual(len(stored_batch.grower.batches), 1)
            self.assertEqual(len(stored_order.buyer.orders), 1)
        finally:
            if session.in_transaction():
                session.rollback()
            if batch_id is not None:
                session.query(Batch).filter_by(id=batch_id).delete(synchronize_session=False)
            if order_id is not None:
                session.query(Order).filter_by(id=order_id).delete(synchronize_session=False)
            if grower_id is not None:
                session.query(Grower).filter_by(id=grower_id).delete(synchronize_session=False)
            if buyer_id is not None:
                session.query(Buyer).filter_by(id=buyer_id).delete(synchronize_session=False)
            session.commit()
            session.close()

    def test_03_foreign_keys_and_nonnegative_quantities_are_enforced(self) -> None:
        session = SessionLocal()
        try:
            session.add(self.batch(uuid4()))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            grower = self.grower()
            buyer = self.buyer()
            session.add_all([grower, buyer])
            session.commit()

            invalid_batch = self.batch(grower.id)
            invalid_batch.expected_kg = Decimal("-0.001")
            session.add(invalid_batch)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            invalid_order = self.order(buyer.id)
            invalid_order.quantity_kg = Decimal("-0.001")
            session.add(invalid_order)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
        finally:
            if session.in_transaction():
                session.rollback()
            if "grower" in locals():
                session.query(Grower).filter_by(id=grower.id).delete(synchronize_session=False)
            if "buyer" in locals():
                session.query(Buyer).filter_by(id=buyer.id).delete(synchronize_session=False)
            session.commit()
            session.close()

    def test_04_parent_records_with_dependents_cannot_be_deleted(self) -> None:
        session = SessionLocal()
        try:
            grower = self.grower()
            buyer = self.buyer()
            session.add_all([grower, buyer])
            session.flush()
            batch = self.batch(grower.id)
            order = self.order(buyer.id)
            session.add_all([batch, order])
            session.commit()

            session.delete(grower)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            session.delete(buyer)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
        finally:
            if session.in_transaction():
                session.rollback()
            if "batch" in locals():
                session.query(Batch).filter_by(id=batch.id).delete(synchronize_session=False)
            if "order" in locals():
                session.query(Order).filter_by(id=order.id).delete(synchronize_session=False)
            if "grower" in locals():
                session.query(Grower).filter_by(id=grower.id).delete(synchronize_session=False)
            if "buyer" in locals():
                session.query(Buyer).filter_by(id=buyer.id).delete(synchronize_session=False)
            session.commit()
            session.close()
