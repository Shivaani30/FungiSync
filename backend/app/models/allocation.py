import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentifierTimestampMixin

if TYPE_CHECKING:
    from app.models.batch import Batch
    from app.models.order import Order


_REASON_WHITESPACE_SQL = (
    r"U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020"
    r"\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006"
    r"\2007\2008\2009\200A\2028\2029\202F\205F\3000'"
)
# Match the f8a3c9d2e641 migration and reservation_service._terminal_reason.
_NONBLANK_REASON_SQL = f"length(btrim(terminal_reason_code, {_REASON_WHITESPACE_SQL})) > 0"


class AllocationProposalStatus(str, enum.Enum):
    """Persisted lifecycle labels only; Phase 3.1 implements no lifecycle behavior."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class AllocationReservationStatus(str, enum.Enum):
    """Lifecycle labels for future reservation operations."""

    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class AllocationOperationIdempotencyState(str, enum.Enum):
    """Persisted idempotency request states supported by this schema foundation."""

    COMPLETED = "COMPLETED"


class AllocationProposal(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_proposals"
    __table_args__ = (
        CheckConstraint(
            "status <> 'PENDING_APPROVAL' OR (submitted_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_allocation_proposals_pending_timestamps",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR (rejected_at IS NOT NULL AND terminal_reason_code IS NOT NULL "
            f"AND {_NONBLANK_REASON_SQL})",
            name="ck_allocation_proposals_rejected_metadata",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND terminal_reason_code IS NOT NULL "
            f"AND {_NONBLANK_REASON_SQL})",
            name="ck_allocation_proposals_cancelled_metadata",
        ),
        CheckConstraint(
            "status <> 'RELEASED' OR (terminal_reason_code IS NOT NULL "
            f"AND {_NONBLANK_REASON_SQL})",
            name="ck_allocation_proposals_released_reason",
        ),
    )

    status: Mapped[AllocationProposalStatus] = mapped_column(
        Enum(AllocationProposalStatus, name="allocation_proposal_status", native_enum=True,
             values_callable=lambda statuses: [status.value for status in statuses]),
        nullable=False, default=AllocationProposalStatus.DRAFT,
        server_default=AllocationProposalStatus.DRAFT.value, index=True,
    )
    ruleset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    versions: Mapped[list["AllocationProposalVersion"]] = relationship(back_populates="proposal", passive_deletes="all")
    events: Mapped[list["AllocationEvent"]] = relationship(back_populates="proposal", passive_deletes="all")
    reservations: Mapped[list["AllocationReservation"]] = relationship(back_populates="proposal", passive_deletes="all")
    idempotency_operations: Mapped[list["AllocationOperationIdempotency"]] = relationship(back_populates="proposal", passive_deletes="all")


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
    reservations: Mapped[list["AllocationReservation"]] = relationship(viewonly=True)
    idempotency_operations: Mapped[list["AllocationOperationIdempotency"]] = relationship(viewonly=True)


class AllocationLine(IdentifierTimestampMixin, Base):
    __tablename__ = "allocation_lines"
    __table_args__ = (
        CheckConstraint("proposed_quantity_kg > 0", name="ck_allocation_lines_quantity_positive"),
        UniqueConstraint("id", "proposal_version_id", name="uq_allocation_lines_id_version"),
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
    reservation: Mapped["AllocationReservation | None"] = relationship(
        back_populates="allocation_line",
        uselist=False,
        primaryjoin="AllocationLine.id == AllocationReservation.allocation_line_id",
        foreign_keys="AllocationReservation.allocation_line_id",
    )


class AllocationReservation(IdentifierTimestampMixin, Base):
    """Schema-only reservation record; Phase 3.3.1 creates no operational reservations."""

    __tablename__ = "allocation_reservations"
    __table_args__ = (
        UniqueConstraint("allocation_line_id", name="uq_allocation_reservations_line_id"),
        CheckConstraint("reserved_quantity_kg > 0", name="ck_allocation_reservations_quantity_positive"),
        CheckConstraint(
            "status <> 'PENDING' OR (expires_at IS NOT NULL AND committed_at IS NULL AND terminal_at IS NULL)",
            name="ck_allocation_reservations_pending_lifecycle",
        ),
        CheckConstraint(
            "status <> 'COMMITTED' OR (committed_at IS NOT NULL AND terminal_at IS NULL)",
            name="ck_allocation_reservations_committed_lifecycle",
        ),
        CheckConstraint(
            "status NOT IN ('REJECTED', 'EXPIRED') OR (committed_at IS NULL AND terminal_at IS NOT NULL)",
            name="ck_allocation_reservations_uncommitted_terminal_lifecycle",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR terminal_at IS NOT NULL",
            name="ck_allocation_reservations_cancelled_lifecycle",
        ),
        CheckConstraint(
            "status <> 'RELEASED' OR (committed_at IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_allocation_reservations_released_lifecycle",
        ),
        CheckConstraint(
            "status NOT IN ('REJECTED', 'CANCELLED') OR (terminal_reason_code IS NOT NULL "
            f"AND {_NONBLANK_REASON_SQL})",
            name="ck_allocation_reservations_rejected_cancelled_reason",
        ),
        ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_reservations_version_proposal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["allocation_line_id", "proposal_version_id"],
            ["allocation_lines.id", "allocation_lines.proposal_version_id"],
            name="fk_allocation_reservations_line_version",
            ondelete="RESTRICT",
        ),
        Index("ix_allocation_reservations_proposal_id", "proposal_id"),
        Index("ix_allocation_reservations_version_id", "proposal_version_id"),
        Index("ix_allocation_reservations_status", "status"),
        Index("ix_allocation_reservations_status_expires_at", "status", "expires_at"),
    )

    allocation_line_id: Mapped[UUID] = mapped_column(nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposals.id", ondelete="RESTRICT"), nullable=False)
    proposal_version_id: Mapped[UUID] = mapped_column(nullable=False)
    reserved_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    status: Mapped[AllocationReservationStatus] = mapped_column(
        Enum(
            AllocationReservationStatus,
            name="allocation_reservation_status",
            native_enum=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    allocation_line: Mapped["AllocationLine"] = relationship(
        back_populates="reservation",
        primaryjoin="AllocationReservation.allocation_line_id == AllocationLine.id",
        foreign_keys=[allocation_line_id],
    )
    proposal: Mapped["AllocationProposal"] = relationship(
        back_populates="reservations",
        foreign_keys=[proposal_id],
    )
    proposal_version: Mapped["AllocationProposalVersion"] = relationship(viewonly=True)
    events: Mapped[list["AllocationEvent"]] = relationship(back_populates="reservation", passive_deletes="all")


class AllocationOperationIdempotency(IdentifierTimestampMixin, Base):
    """Schema-only operation identity and result record for future lifecycle services."""

    __tablename__ = "allocation_operation_idempotency"
    __table_args__ = (
        UniqueConstraint("operation_type", "idempotency_key", name="uq_allocation_operation_idempotency_type_key"),
        CheckConstraint("length(trim(operation_type)) > 0", name="ck_allocation_operation_idempotency_type_not_blank"),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_allocation_operation_idempotency_key_not_blank"),
        CheckConstraint("length(trim(request_fingerprint)) > 0", name="ck_allocation_operation_idempotency_fingerprint_not_blank"),
        ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_operation_idempotency_version_proposal",
            ondelete="RESTRICT",
        ),
        Index("ix_allocation_operation_idempotency_proposal_id", "proposal_id"),
    )

    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposals.id", ondelete="RESTRICT"), nullable=False)
    proposal_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[AllocationOperationIdempotencyState] = mapped_column(
        Enum(
            AllocationOperationIdempotencyState,
            name="allocation_operation_idempotency_state",
            native_enum=True,
            values_callable=lambda states: [state.value for state in states],
        ),
        nullable=False,
    )
    result_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("allocation_events.id", ondelete="RESTRICT"), nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    proposal: Mapped["AllocationProposal"] = relationship(
        back_populates="idempotency_operations",
        foreign_keys=[proposal_id],
    )
    proposal_version: Mapped["AllocationProposalVersion | None"] = relationship(viewonly=True)
    result_event: Mapped["AllocationEvent | None"] = relationship(foreign_keys=[result_event_id])


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
        Index("ix_allocation_events_reservation_id", "reservation_id"),
    )

    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("allocation_proposals.id", ondelete="RESTRICT"), nullable=False)
    proposal_version_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reservation_id: Mapped[UUID | None] = mapped_column(ForeignKey("allocation_reservations.id", ondelete="RESTRICT"), nullable=True)

    proposal: Mapped["AllocationProposal"] = relationship(back_populates="events")
    proposal_version: Mapped["AllocationProposalVersion"] = relationship(
        viewonly=True,
    )
    reservation: Mapped["AllocationReservation | None"] = relationship(back_populates="events")
