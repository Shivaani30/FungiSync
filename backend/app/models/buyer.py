from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin

if TYPE_CHECKING:
    from app.models.order import Order


class Buyer(IdentifierTimestampMixin, Base):
    __tablename__ = "buyers"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_buyers_name_not_blank"),
        CheckConstraint("length(trim(location)) > 0", name="ck_buyers_location_not_blank"),
        CheckConstraint("length(trim(contact)) > 0", name="ck_buyers_contact_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    contact: Mapped[str] = mapped_column(String(200), nullable=False)

    orders: Mapped[list["Order"]] = relationship(
        back_populates="buyer",
        passive_deletes="all",
    )
