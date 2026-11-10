import enum
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin
from app.models.quality import AllocationQualityGrade

if TYPE_CHECKING:
    from app.models.allocation import AllocationLine
    from app.models.grower import Grower


class BatchStatus(str, enum.Enum):
    EXPECTED = "expected"
    CONFIRMED = "confirmed"
    HARVESTED = "harvested"
    CANCELLED = "cancelled"


class Batch(IdentifierTimestampMixin, Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("length(trim(species)) > 0", name="ck_batches_species_not_blank"),
        CheckConstraint("expected_kg >= 0", name="ck_batches_expected_kg_nonnegative"),
        CheckConstraint(
            "confirmed_kg IS NULL OR confirmed_kg >= 0",
            name="ck_batches_confirmed_kg_nonnegative",
        ),
        CheckConstraint(
            "asking_price_per_kg IS NULL OR asking_price_per_kg >= 0",
            name="ck_batches_asking_price_per_kg_nonnegative",
        ),
        CheckConstraint(
            "quality_grade IS NULL OR length(trim(quality_grade)) > 0",
            name="ck_batches_quality_grade_not_blank",
        ),
        CheckConstraint(
            "best_before_date IS NULL OR best_before_date >= harvest_date",
            name="ck_batches_best_before_on_or_after_harvest",
        ),
        Index("ix_batches_grower_status_harvest_date", "grower_id", "status", "harvest_date"),
    )

    grower_id: Mapped[UUID] = mapped_column(
        ForeignKey("growers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    species: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    expected_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    confirmed_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    asking_price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    quality_grade: Mapped[str | None] = mapped_column(String(30), nullable=True)
    allocation_quality_grade: Mapped[AllocationQualityGrade | None] = mapped_column(
        Enum(
            AllocationQualityGrade,
            name="allocation_quality_grade",
            native_enum=True,
            values_callable=lambda grades: [grade.value for grade in grades],
        ),
        nullable=True,
        index=True,
    )
    best_before_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(
            BatchStatus,
            name="batch_status",
            native_enum=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=BatchStatus.EXPECTED,
        server_default=BatchStatus.EXPECTED.value,
        index=True,
    )

    grower: Mapped["Grower"] = relationship(back_populates="batches")
    allocation_lines: Mapped[list["AllocationLine"]] = relationship(
        back_populates="batch",
        passive_deletes="all",
    )
