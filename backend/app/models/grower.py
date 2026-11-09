from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin

if TYPE_CHECKING:
    from app.models.batch import Batch


class Grower(IdentifierTimestampMixin, Base):
    __tablename__ = "growers"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_growers_name_not_blank"),
        CheckConstraint("length(trim(phone)) > 0", name="ck_growers_phone_not_blank"),
        CheckConstraint("length(trim(location)) > 0", name="ck_growers_location_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    batches: Mapped[list["Batch"]] = relationship(
        back_populates="grower",
        passive_deletes="all",
    )
