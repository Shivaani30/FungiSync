"""add allocation data foundation

Revision ID: a61f9b3d7e82
Revises: 3585205556e4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a61f9b3d7e82"
down_revision: Union[str, Sequence[str], None] = "3585205556e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


allocation_quality_grade = postgresql.ENUM(
    "PREMIUM",
    "STANDARD",
    "PROCESSING",
    "REJECTED",
    name="allocation_quality_grade",
    create_type=False,
)
allocation_proposal_status = postgresql.ENUM(
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
    "RELEASED",
    name="allocation_proposal_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    allocation_quality_grade.create(bind, checkfirst=True)
    allocation_proposal_status.create(bind, checkfirst=True)

    op.add_column(
        "batches",
        sa.Column("allocation_quality_grade", allocation_quality_grade, nullable=True),
    )
    op.create_index("ix_batches_allocation_quality_grade", "batches", ["allocation_quality_grade"], unique=False)
    op.add_column(
        "orders",
        sa.Column("allocation_required_quality_grade", allocation_quality_grade, nullable=True),
    )
    op.create_index(
        "ix_orders_allocation_required_quality_grade",
        "orders",
        ["allocation_required_quality_grade"],
        unique=False,
    )

    op.create_table(
        "allocation_proposals",
        sa.Column("status", allocation_proposal_status, server_default="DRAFT", nullable=False),
        sa.Column("ruleset_version", sa.String(length=50), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_allocation_proposals_status", "allocation_proposals", ["status"], unique=False)

    op.create_table(
        "allocation_proposal_versions",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_allocation_proposal_versions_number_positive"),
        sa.ForeignKeyConstraint(["proposal_id"], ["allocation_proposals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "proposal_id", name="uq_allocation_proposal_versions_id_proposal"),
        sa.UniqueConstraint("proposal_id", "version_number", name="uq_allocation_proposal_versions_proposal_version"),
    )
    op.create_index("ix_allocation_proposal_versions_proposal_id", "allocation_proposal_versions", ["proposal_id"], unique=False)

    op.create_table(
        "allocation_lines",
        sa.Column("proposal_version_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_quantity_kg", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("proposed_quantity_kg > 0", name="ck_allocation_lines_quantity_positive"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposal_version_id"], ["allocation_proposal_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_allocation_lines_version_id", "allocation_lines", ["proposal_version_id"], unique=False)
    op.create_index("ix_allocation_lines_batch_id", "allocation_lines", ["batch_id"], unique=False)
    op.create_index("ix_allocation_lines_order_id", "allocation_lines", ["order_id"], unique=False)

    op.create_table(
        "allocation_events",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_version_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(trim(event_type)) > 0", name="ck_allocation_events_type_not_blank"),
        sa.ForeignKeyConstraint(["proposal_id"], ["allocation_proposals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_events_version_proposal",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_allocation_events_proposal_id", "allocation_events", ["proposal_id"], unique=False)
    op.create_index("ix_allocation_events_version_id", "allocation_events", ["proposal_version_id"], unique=False)
    op.create_index("ix_allocation_events_event_type", "allocation_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_allocation_events_event_type", table_name="allocation_events")
    op.drop_index("ix_allocation_events_version_id", table_name="allocation_events")
    op.drop_index("ix_allocation_events_proposal_id", table_name="allocation_events")
    op.drop_table("allocation_events")
    op.drop_index("ix_allocation_lines_order_id", table_name="allocation_lines")
    op.drop_index("ix_allocation_lines_batch_id", table_name="allocation_lines")
    op.drop_index("ix_allocation_lines_version_id", table_name="allocation_lines")
    op.drop_table("allocation_lines")
    op.drop_index("ix_allocation_proposal_versions_proposal_id", table_name="allocation_proposal_versions")
    op.drop_table("allocation_proposal_versions")
    op.drop_index("ix_allocation_proposals_status", table_name="allocation_proposals")
    op.drop_table("allocation_proposals")
    op.drop_index("ix_orders_allocation_required_quality_grade", table_name="orders")
    op.drop_column("orders", "allocation_required_quality_grade")
    op.drop_index("ix_batches_allocation_quality_grade", table_name="batches")
    op.drop_column("batches", "allocation_quality_grade")

    bind = op.get_bind()
    allocation_proposal_status.drop(bind, checkfirst=True)
    allocation_quality_grade.drop(bind, checkfirst=True)
