import enum
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin

if TYPE_CHECKING:
    from app.models.buyer import Buyer


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class Order(IdentifierTimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("length(trim(species)) > 0", name="ck_orders_species_not_blank"),
        CheckConstraint("quantity_kg >= 0", name="ck_orders_quantity_kg_nonnegative"),
        CheckConstraint("price_limit >= 0", name="ck_orders_price_limit_nonnegative"),
        CheckConstraint(
            "required_quality_grade IS NULL OR length(trim(required_quality_grade)) > 0",
            name="ck_orders_required_quality_grade_not_blank",
        ),
        Index("ix_orders_buyer_status_delivery_date", "buyer_id", "status", "delivery_date"),
    )

    buyer_id: Mapped[UUID] = mapped_column(
        ForeignKey("buyers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    species: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    price_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    required_quality_grade: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=OrderStatus.PENDING,
        server_default=OrderStatus.PENDING.value,
        index=True,
    )

    buyer: Mapped["Buyer"] = relationship(back_populates="orders")
