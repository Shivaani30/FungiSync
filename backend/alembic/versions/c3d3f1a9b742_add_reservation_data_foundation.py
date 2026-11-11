"""add reservation data foundation

Revision ID: c3d3f1a9b742
Revises: a61f9b3d7e82
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3d3f1a9b742"
down_revision: Union[str, Sequence[str], None] = "a61f9b3d7e82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


allocation_reservation_status = postgresql.ENUM(
    "PENDING",
    "COMMITTED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
    "RELEASED",
    name="allocation_reservation_status",
    create_type=False,
)
allocation_operation_idempotency_state = postgresql.ENUM(
    "COMPLETED",
    name="allocation_operation_idempotency_state",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    allocation_reservation_status.create(bind, checkfirst=True)
    allocation_operation_idempotency_state.create(bind, checkfirst=True)

    op.add_column("allocation_proposals", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("allocation_proposals", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("allocation_proposals", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("allocation_proposals", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("allocation_proposals", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("allocation_proposals", sa.Column("terminal_reason_code", sa.String(length=100), nullable=True))
    op.add_column("allocation_proposals", sa.Column("terminal_reason", sa.Text(), nullable=True))

    op.create_unique_constraint(
        "uq_allocation_lines_id_version",
        "allocation_lines",
        ["id", "proposal_version_id"],
    )

    op.create_table(
        "allocation_reservations",
        sa.Column("allocation_line_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_version_id", sa.Uuid(), nullable=False),
        sa.Column("reserved_quantity_kg", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("status", allocation_reservation_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason_code", sa.String(length=100), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("reserved_quantity_kg > 0", name="ck_allocation_reservations_quantity_positive"),
        sa.CheckConstraint(
            "status <> 'PENDING' OR (expires_at IS NOT NULL AND committed_at IS NULL AND terminal_at IS NULL)",
            name="ck_allocation_reservations_pending_lifecycle",
        ),
        sa.CheckConstraint(
            "status <> 'COMMITTED' OR (committed_at IS NOT NULL AND terminal_at IS NULL)",
            name="ck_allocation_reservations_committed_lifecycle",
        ),
        sa.CheckConstraint(
            "status NOT IN ('REJECTED', 'EXPIRED') OR (committed_at IS NULL AND terminal_at IS NOT NULL)",
            name="ck_allocation_reservations_uncommitted_terminal_lifecycle",
        ),
        sa.CheckConstraint(
            "status <> 'CANCELLED' OR terminal_at IS NOT NULL",
            name="ck_allocation_reservations_cancelled_lifecycle",
        ),
        sa.CheckConstraint(
            "status <> 'RELEASED' OR (committed_at IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_allocation_reservations_released_lifecycle",
        ),
        sa.ForeignKeyConstraint(["proposal_id"], ["allocation_proposals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_reservations_version_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_line_id", "proposal_version_id"],
            ["allocation_lines.id", "allocation_lines.proposal_version_id"],
            name="fk_allocation_reservations_line_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("allocation_line_id", name="uq_allocation_reservations_line_id"),
    )
    op.create_index("ix_allocation_reservations_proposal_id", "allocation_reservations", ["proposal_id"], unique=False)
    op.create_index("ix_allocation_reservations_version_id", "allocation_reservations", ["proposal_version_id"], unique=False)
    op.create_index("ix_allocation_reservations_status", "allocation_reservations", ["status"], unique=False)
    op.create_index("ix_allocation_reservations_status_expires_at", "allocation_reservations", ["status", "expires_at"], unique=False)
    op.execute("""
        -- Preserve advisory allocation-line identity and quantity once a reservation
        -- snapshots it; reservation lifecycle state/timestamps remain updatable.
        CREATE FUNCTION enforce_allocation_line_reservation_snapshot()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM allocation_reservations
                WHERE allocation_line_id = OLD.id
            ) AND (NEW.id IS DISTINCT FROM OLD.id
                 OR NEW.proposal_version_id IS DISTINCT FROM OLD.proposal_version_id
                 OR NEW.batch_id IS DISTINCT FROM OLD.batch_id
                 OR NEW.order_id IS DISTINCT FROM OLD.order_id
                 OR NEW.proposed_quantity_kg IS DISTINCT FROM OLD.proposed_quantity_kg) THEN
                RAISE EXCEPTION 'reserved allocation line snapshot fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_lines_reservation_snapshot
        BEFORE UPDATE OF id, proposal_version_id, batch_id, order_id, proposed_quantity_kg ON allocation_lines
        FOR EACH ROW EXECUTE FUNCTION enforce_allocation_line_reservation_snapshot();
    """)
    op.execute("""
        CREATE FUNCTION enforce_allocation_reservation_snapshot()
        RETURNS trigger AS $$
        DECLARE
            line_quantity numeric(12, 3);
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (NEW.allocation_line_id IS DISTINCT FROM OLD.allocation_line_id
                    OR NEW.proposal_id IS DISTINCT FROM OLD.proposal_id
                    OR NEW.proposal_version_id IS DISTINCT FROM OLD.proposal_version_id
                    OR NEW.reserved_quantity_kg IS DISTINCT FROM OLD.reserved_quantity_kg) THEN
                RAISE EXCEPTION 'allocation reservation snapshot fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            -- FOR SHARE conflicts with an allocation-line UPDATE lock.  This
            -- serializes reservation snapshot validation with line changes.
            SELECT proposed_quantity_kg
            INTO line_quantity
            FROM allocation_lines
            WHERE id = NEW.allocation_line_id
              AND proposal_version_id = NEW.proposal_version_id
            FOR SHARE;
            IF NOT FOUND OR line_quantity <> NEW.reserved_quantity_kg THEN
                RAISE EXCEPTION 'reservation quantity must equal its allocation line proposed quantity'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_reservations_snapshot
        BEFORE INSERT OR UPDATE ON allocation_reservations
        FOR EACH ROW EXECUTE FUNCTION enforce_allocation_reservation_snapshot();
    """)

    op.create_table(
        "allocation_operation_idempotency",
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_version_id", sa.Uuid(), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("state", allocation_operation_idempotency_state, nullable=False),
        sa.Column("result_event_id", sa.Uuid(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(trim(operation_type)) > 0", name="ck_allocation_operation_idempotency_type_not_blank"),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_allocation_operation_idempotency_key_not_blank"),
        sa.CheckConstraint("length(trim(request_fingerprint)) > 0", name="ck_allocation_operation_idempotency_fingerprint_not_blank"),
        sa.ForeignKeyConstraint(["proposal_id"], ["allocation_proposals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["proposal_version_id", "proposal_id"],
            ["allocation_proposal_versions.id", "allocation_proposal_versions.proposal_id"],
            name="fk_allocation_operation_idempotency_version_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["result_event_id"], ["allocation_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_type", "idempotency_key", name="uq_allocation_operation_idempotency_type_key"),
    )
    op.create_index("ix_allocation_operation_idempotency_proposal_id", "allocation_operation_idempotency", ["proposal_id"], unique=False)

    op.add_column("allocation_events", sa.Column("reservation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_allocation_events_reservation_id",
        "allocation_events",
        "allocation_reservations",
        ["reservation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_allocation_events_reservation_id", "allocation_events", ["reservation_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_allocation_events_reservation_id", table_name="allocation_events")
    op.drop_constraint("fk_allocation_events_reservation_id", "allocation_events", type_="foreignkey")
    op.drop_column("allocation_events", "reservation_id")

    op.drop_index("ix_allocation_operation_idempotency_proposal_id", table_name="allocation_operation_idempotency")
    op.drop_table("allocation_operation_idempotency")

    op.drop_index("ix_allocation_reservations_status_expires_at", table_name="allocation_reservations")
    op.drop_index("ix_allocation_reservations_status", table_name="allocation_reservations")
    op.drop_index("ix_allocation_reservations_version_id", table_name="allocation_reservations")
    op.drop_index("ix_allocation_reservations_proposal_id", table_name="allocation_reservations")
    op.execute("DROP TRIGGER trg_allocation_reservations_snapshot ON allocation_reservations")
    op.execute("DROP FUNCTION enforce_allocation_reservation_snapshot()")
    op.execute("DROP TRIGGER trg_allocation_lines_reservation_snapshot ON allocation_lines")
    op.execute("DROP FUNCTION enforce_allocation_line_reservation_snapshot()")
    op.drop_table("allocation_reservations")
    op.drop_constraint("uq_allocation_lines_id_version", "allocation_lines", type_="unique")

    op.drop_column("allocation_proposals", "terminal_reason")
    op.drop_column("allocation_proposals", "terminal_reason_code")
    op.drop_column("allocation_proposals", "expires_at")
    op.drop_column("allocation_proposals", "cancelled_at")
    op.drop_column("allocation_proposals", "rejected_at")
    op.drop_column("allocation_proposals", "approved_at")
    op.drop_column("allocation_proposals", "submitted_at")

    bind = op.get_bind()
    allocation_operation_idempotency_state.drop(bind, checkfirst=True)
    allocation_reservation_status.drop(bind, checkfirst=True)
