import enum
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin

if TYPE_CHECKING:
    from app.models.batch import Batch
    from app.models.order import Order


class AllocationProposalStatus(str, enum.Enum):
    """Persisted lifecycle labels only; Phase 3.1 implements no lifecycle behavior."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class AllocationProposal(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_proposals"

    status: Mapped[AllocationProposalStatus] = mapped_column(
        Enum(AllocationProposalStatus, name="allocation_proposal_status", native_enum=True,
             values_callable=lambda statuses: [status.value for status in statuses]),
        nullable=False, default=AllocationProposalStatus.DRAFT,
        server_default=AllocationProposalStatus.DRAFT.value, index=True,
    )
    ruleset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    versions: Mapped[list["AllocationProposalVersion"]] = relationship(back_populates="proposal", passive_deletes="all")
    events: Mapped[list["AllocationEvent"]] = relationship(back_populates="proposal", passive_deletes="all")


class AllocationProposalVersion(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_proposal_versions"
    __table_args__ = (
        UniqueConstraint("proposal_id", "version_number", name="uq_allocation_proposal_versions_proposal_version"),
        UniqueConstraint("id", "proposal_id", name="uq_allocation_proposal_versions_id_proposal"),
        CheckConstraint("version_number > 0", name="ck_allocation_proposal_versions_number_positive"),
        Index("ix_allocation_proposal_versions_proposal_id", "proposal_id"),
    )

    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposals.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    proposal: Mapped["AllocationProposal"] = relationship(back_populates="versions")
    lines: Mapped[list["AllocationLine"]] = relationship(back_populates="proposal_version", passive_deletes="all")
    events: Mapped[list["AllocationEvent"]] = relationship(viewonly=True)


class AllocationLine(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_lines"
    __table_args__ = (
        CheckConstraint("proposed_quantity_kg > 0", name="ck_allocation_lines_quantity_positive"),
        Index("ix_allocation_lines_version_id", "proposal_version_id"),
        Index("ix_allocation_lines_batch_id", "batch_id"),
        Index("ix_allocation_lines_order_id", "order_id"),
    )

    proposal_version_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposal_versions.id", ondelete="RESTRICT"), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False)
    proposed_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)

    proposal_version: Mapped["AllocationProposalVersion"] = relationship(back_populates="lines")
    batch: Mapped["Batch"] = relationship(back_populates="allocation_lines")
    order: Mapped["Order"] = relationship(back_populates="allocation_lines")


class AllocationEvent(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_events"
    __table_args__ = (
        CheckConstraint("length(trim(event_type)) > 0", name="ck_allocation_events_type_not_blank"),
        ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_events_version_proposal",
            ondelete="RESTRICT",
        ),
        Index("ix_allocation_events_proposal_id", "proposal_id"),
        Index("ix_allocation_events_version_id", "proposal_version_id"),
        Index("ix_allocation_events_event_type", "event_type"),
    )

    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposals.id", ondelete="RESTRICT"), nullable=False)
    proposal_version_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    proposal: Mapped["AllocationProposal"] = relationship(back_populates="events")
    proposal_version: Mapped["AllocationProposalVersion"] = relationship(
        viewonly=True,
    )
