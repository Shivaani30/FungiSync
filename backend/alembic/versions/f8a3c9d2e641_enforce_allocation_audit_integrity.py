"""enforce allocation event and draft-line integrity

Revision ID: f8a3c9d2e641
Revises: d7e4b8c2a913
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f8a3c9d2e641"
down_revision: Union[str, Sequence[str], None] = "d7e4b8c2a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Match the explicit whitespace set used by reservation_service._terminal_reason.
# PostgreSQL's one-argument btrim removes spaces only. Unicode escapes avoid
# collation-dependent regular-expression whitespace classes.
_REASON_WHITESPACE_SQL = (
    r"U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020"
    r"\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006"
    r"\2007\2008\2009\200A\2028\2029\202F\205F\3000'"
)
_NONBLANK_REASON_SQL = f"length(btrim(terminal_reason_code, {_REASON_WHITESPACE_SQL})) > 0"


def upgrade() -> None:
    # Each CHECK validates existing rows as well as subsequent INSERT/UPDATE
    # operations. PostgreSQL rolls the migration back if historical data fails.
    op.create_check_constraint(
        "ck_allocation_proposals_pending_timestamps", "allocation_proposals",
        "status <> 'PENDING_APPROVAL' OR (submitted_at IS NOT NULL AND expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_allocation_proposals_rejected_metadata", "allocation_proposals",
        "status <> 'REJECTED' OR (rejected_at IS NOT NULL AND terminal_reason_code IS NOT NULL "
        f"AND {_NONBLANK_REASON_SQL})",
    )
    op.create_check_constraint(
        "ck_allocation_proposals_cancelled_metadata", "allocation_proposals",
        "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND terminal_reason_code IS NOT NULL "
        f"AND {_NONBLANK_REASON_SQL})",
    )
    op.create_check_constraint(
        "ck_allocation_proposals_released_reason", "allocation_proposals",
        "status <> 'RELEASED' OR (terminal_reason_code IS NOT NULL "
        f"AND {_NONBLANK_REASON_SQL})",
    )
    op.create_check_constraint(
        "ck_allocation_reservations_rejected_cancelled_reason", "allocation_reservations",
        "status NOT IN ('REJECTED', 'CANCELLED') OR (terminal_reason_code IS NOT NULL "
        f"AND {_NONBLANK_REASON_SQL})",
    )

    op.execute("""
        CREATE FUNCTION enforce_allocation_event_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'allocation events are append-only; % is prohibited', TG_OP
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_events_append_only
        BEFORE UPDATE OR DELETE ON allocation_events
        FOR EACH ROW EXECUTE FUNCTION enforce_allocation_event_append_only();
    """)
    op.execute("""
        CREATE FUNCTION enforce_allocation_event_no_truncate()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'allocation events are append-only; TRUNCATE is prohibited'
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_events_no_truncate
        BEFORE TRUNCATE ON allocation_events
        FOR EACH STATEMENT EXECUTE FUNCTION enforce_allocation_event_no_truncate();
    """)

    op.execute("""
        CREATE FUNCTION enforce_draft_allocation_line_insert()
        RETURNS trigger AS $$
        DECLARE
            owning_proposal_id uuid;
            proposal_status text;
        BEGIN
            -- The version is immutable under the preceding migration. Read its
            -- proposal ID before taking row locks in the global target order.
            SELECT proposal_id INTO owning_proposal_id
            FROM allocation_proposal_versions
            WHERE id = NEW.proposal_version_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'allocation line references a missing proposal version'
                    USING ERRCODE = '23503';
            END IF;

            PERFORM 1 FROM batches WHERE id = NEW.batch_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'allocation line references a missing batch'
                    USING ERRCODE = '23503';
            END IF;
            PERFORM 1 FROM orders WHERE id = NEW.order_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'allocation line references a missing order'
                    USING ERRCODE = '23503';
            END IF;
            SELECT status::text INTO proposal_status
            FROM allocation_proposals
            WHERE id = owning_proposal_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'allocation line references a missing proposal'
                    USING ERRCODE = '23503';
            END IF;
            IF proposal_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'allocation lines can only be inserted for DRAFT proposals'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_lines_draft_insert
        BEFORE INSERT ON allocation_lines
        FOR EACH ROW EXECUTE FUNCTION enforce_draft_allocation_line_insert();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_allocation_lines_draft_insert ON allocation_lines")
    op.execute("DROP FUNCTION enforce_draft_allocation_line_insert()")
    op.execute("DROP TRIGGER trg_allocation_events_no_truncate ON allocation_events")
    op.execute("DROP FUNCTION enforce_allocation_event_no_truncate()")
    op.execute("DROP TRIGGER trg_allocation_events_append_only ON allocation_events")
    op.execute("DROP FUNCTION enforce_allocation_event_append_only()")
    op.drop_constraint("ck_allocation_reservations_rejected_cancelled_reason", "allocation_reservations", type_="check")
    op.drop_constraint("ck_allocation_proposals_released_reason", "allocation_proposals", type_="check")
    op.drop_constraint("ck_allocation_proposals_cancelled_metadata", "allocation_proposals", type_="check")
    op.drop_constraint("ck_allocation_proposals_rejected_metadata", "allocation_proposals", type_="check")
    op.drop_constraint("ck_allocation_proposals_pending_timestamps", "allocation_proposals", type_="check")
