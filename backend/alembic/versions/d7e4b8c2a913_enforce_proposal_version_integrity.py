"""enforce single immutable allocation proposal version

Revision ID: d7e4b8c2a913
Revises: c3d3f1a9b742
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d7e4b8c2a913"
down_revision: Union[str, Sequence[str], None] = "c3d3f1a9b742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION enforce_allocation_proposal_version_immutable()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'allocation proposal versions are immutable; % is prohibited', TG_OP
                USING ERRCODE = '23514';
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_proposal_versions_immutable
        BEFORE UPDATE OR DELETE ON allocation_proposal_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_allocation_proposal_version_immutable();
    """)

    op.execute("""
        CREATE FUNCTION enforce_initial_allocation_proposal_version()
        RETURNS trigger AS $$
        DECLARE
            proposal_status text;
        BEGIN
            -- Locking the owning proposal serializes version creation with other
            -- inserts and with reservation lifecycle transitions on that proposal.
            SELECT status::text
            INTO proposal_status
            FROM allocation_proposals
            WHERE id = NEW.proposal_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'allocation proposal version references a missing proposal'
                    USING ERRCODE = '23514';
            END IF;
            IF proposal_status <> 'DRAFT' THEN
                RAISE EXCEPTION 'allocation proposal versions can only be created for DRAFT proposals'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version_number <> 1 THEN
                RAISE EXCEPTION 'single-version policy requires proposal version number 1'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM allocation_reservations
                WHERE proposal_id = NEW.proposal_id
            ) THEN
                RAISE EXCEPTION 'allocation proposal version cannot be created after reservation history exists'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM allocation_proposal_versions
                WHERE proposal_id = NEW.proposal_id
            ) THEN
                RAISE EXCEPTION 'allocation proposal already has its single permitted version'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_allocation_proposal_versions_initial_only
        BEFORE INSERT ON allocation_proposal_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_initial_allocation_proposal_version();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_allocation_proposal_versions_initial_only ON allocation_proposal_versions")
    op.execute("DROP FUNCTION enforce_initial_allocation_proposal_version()")
    op.execute("DROP TRIGGER trg_allocation_proposal_versions_immutable ON allocation_proposal_versions")
    op.execute("DROP FUNCTION enforce_allocation_proposal_version_immutable()")
