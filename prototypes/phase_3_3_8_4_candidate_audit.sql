-- Phase 3.3.8.4 diagnostic prototype. READ ONLY. Never a migration or trigger.
-- Run only after independently verifying the connection targets an isolated
-- PostgreSQL test database with the prerequisite migrations installed.
-- These are candidate policies, not approved database constraints.

WITH violations AS (
    SELECT p.id AS proposal_id, 'draft_has_reservation' AS code
    FROM allocation_proposals AS p
    WHERE p.status = 'DRAFT'
      AND EXISTS (
          SELECT 1 FROM allocation_reservations AS r
          WHERE r.proposal_id = p.id
      )

    UNION ALL
    SELECT p.id, 'active_or_terminal_missing_single_version'
    FROM allocation_proposals AS p
    WHERE p.status <> 'DRAFT'
      AND (SELECT count(*) FROM allocation_proposal_versions AS v
           WHERE v.proposal_id = p.id) <> 1

    UNION ALL
    SELECT p.id, 'active_or_terminal_has_no_line'
    FROM allocation_proposals AS p
    WHERE p.status <> 'DRAFT'
      AND NOT EXISTS (
          SELECT 1 FROM allocation_proposal_versions AS v
          JOIN allocation_lines AS l ON l.proposal_version_id = v.id
          WHERE v.proposal_id = p.id
      )

    UNION ALL
    SELECT DISTINCT p.id, 'submitted_line_missing_reservation'
    FROM allocation_proposals AS p
    JOIN allocation_proposal_versions AS v ON v.proposal_id = p.id
    JOIN allocation_lines AS l ON l.proposal_version_id = v.id
    LEFT JOIN allocation_reservations AS r ON r.allocation_line_id = l.id
    WHERE p.status <> 'DRAFT' AND r.id IS NULL

    UNION ALL
    SELECT DISTINCT p.id, 'reservation_status_disagrees_with_proposal'
    FROM allocation_proposals AS p
    JOIN allocation_reservations AS r ON r.proposal_id = p.id
    WHERE (p.status = 'PENDING_APPROVAL' AND r.status <> 'PENDING')
       OR (p.status = 'APPROVED' AND r.status <> 'COMMITTED')
       OR (p.status = 'REJECTED' AND r.status <> 'REJECTED')
       OR (p.status = 'CANCELLED' AND r.status <> 'CANCELLED')
       OR (p.status = 'EXPIRED' AND r.status <> 'EXPIRED')
       OR (p.status = 'RELEASED' AND r.status <> 'RELEASED')

    UNION ALL
    SELECT DISTINCT p.id, 'pending_expiry_disagrees_with_proposal'
    FROM allocation_proposals AS p
    JOIN allocation_reservations AS r ON r.proposal_id = p.id
    WHERE p.status = 'PENDING_APPROVAL'
      AND r.expires_at IS DISTINCT FROM p.expires_at

    UNION ALL
    SELECT DISTINCT p.id, 'cancelled_reservations_mix_commit_history'
    FROM allocation_proposals AS p
    JOIN allocation_reservations AS r ON r.proposal_id = p.id
    WHERE p.status = 'CANCELLED'
    GROUP BY p.id
    HAVING count(DISTINCT (r.committed_at IS NOT NULL)) > 1

    UNION ALL
    SELECT DISTINCT e.proposal_id, 'event_reservation_owner_mismatch'
    FROM allocation_events AS e
    JOIN allocation_reservations AS r ON r.id = e.reservation_id
    WHERE e.proposal_id IS DISTINCT FROM r.proposal_id
       OR e.proposal_version_id IS DISTINCT FROM r.proposal_version_id
)
SELECT proposal_id, code
FROM violations
ORDER BY proposal_id, code;
