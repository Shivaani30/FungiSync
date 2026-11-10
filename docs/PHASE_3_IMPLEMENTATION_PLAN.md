# Phase 3 — Coordinator-Reviewed Allocation Implementation Plan

> **Status:** Plan only. This document does not authorize implementation, schema changes, endpoints, or frontend changes.
>
> **Phase 2.1 baseline:** The finalized [Phase 2.1 business rules](PHASE_2_1_BUSINESS_RULES.md) are binding for this plan. Phase 2 matching remains deterministic, read-only candidate discovery. It is not allocation and must remain unchanged.

## 1. Current verified baseline

- The backend is FastAPI, SQLAlchemy, PostgreSQL, Alembic, and Pydantic.
- Persisted operational tables are currently only growers, batches, buyers, and orders.
- Batch quantity is stored as `NUMERIC(12,3)`-compatible `confirmed_kg`; it has no reservation or allocation state.
- Quality fields are optional free text. Phase 2 matching compares known grades with exact normalized text equality and marks missing values unknown.
- Existing matching endpoints are `GET /matches/orders/{order_id}` and `GET /matches/batches/{batch_id}`; they produce explanations but do not mutate data.
- The React matching workspace is a read-only consumer of those endpoints.
- There is no genuine user identity, authentication, role, or authorization system.

## 2. Implementation dependency diagram and recommended order

```text
Phase 3.1 Data model + migration strategy
       |--------------------|
       v                    v
Phase 3.2 Draft engine   Phase 3.3 Quantity/reservation service
       |                    |
       |                    |
       +---------+----------+
                 v
Phase 3.4 Genuine authentication and authorization prerequisite
                 |
                 v
Phase 3.5 Authenticated coordinator approval/allocation APIs
                 |
                 v
Phase 3.6 Tests and React coordinator workspace
```

Recommended order: complete 3.1 first; build 3.2 and 3.3 with unit/integration tests; complete 3.4 before exposing any operational approval write; then 3.5; finish with 3.6. Draft generation may be developed before authentication, but must remain advisory and must not reserve or approve stock.

## 3. Cross-cutting non-negotiable rules

- Use `Decimal` in Python and PostgreSQL `NUMERIC(12,3)`-compatible arithmetic for quantity calculations. Never use floating-point arithmetic for quantity accounting.
- Calculate available batch quantity initially from persisted active reservation and committed allocation lines; do not trust client totals or a browser cache.
- `available = confirmed - active_reservations - active_committed_allocations` must never be negative.
- Total active reservations plus committed allocations may never exceed confirmed quantity. The equivalent aggregate order limit prevents overfulfillment.
- Drafts are read-only/advisory. Submission creates configurable, default 30-minute reservations. Expired reservations must safely transition before they are treated as available.
- All allocation-changing operations use PostgreSQL transactions, deterministic row-locking rules, revalidation, append-only audit events, and idempotency keys.
- Approved history is never deleted or silently overwritten. Release affects only remaining unconsumed committed quantity. Allocated, dispatched, and delivered quantities remain separate concepts; dispatch/delivery are later-phase work.
- Unknown price, quality, or best-before date blocks automatic allocation. Legacy free-text grades are preserved, then mapped only after explicit manual confirmation; ambiguous or unreviewed values remain `Unknown/unmapped`. No migration or service may infer that `Grade A`, `Export Quality`, or similar text means Premium or Standard.
- Do not alter Phase 2 score behavior, free-text matching behavior, or matching endpoints while implementing Phase 3.

### Approved allocation quality compatibility

This is the same approved allocation matrix in the Phase 2.1 rules. It applies only to the separate controlled allocation-grade representation, never retroactively to Phase 2 free-text matching.

| Batch grade | Premium requirement | Standard requirement | Processing requirement |
|---|---|---|---|
| Premium | Allowed | Allowed | Not allowed |
| Standard | Not allowed | Allowed | Not allowed |
| Processing | Not allowed | Not allowed | Allowed |
| Rejected | Not allowed | Not allowed | Not allowed |
| Unknown/unmapped | Not allowed | Not allowed | Not allowed |

Premium may satisfy Standard demand; no downgrade is permitted. Rejected and `Unknown/unmapped` grades cannot be automatically allocated.

### Decision status

**Approved:** quality matrix and conservative unknown-data blocks; manual-confirmation-only controlled-grade mapping; split/partial allocation with no overfulfillment; Draft-no-reservation and configurable default 30-minute submission reservation; exactly-once rejection/cancellation/expiry release; all-or-nothing revalidated approval with preserved version history; Decimal accounting, persisted-line availability, append-only events, locking, and idempotency; and authentication/authorization before operational coordinator writes.

**Still unresolved (do not infer):** trusted actor permissions and separation of duties, approval thresholds, override policy/evidence, release and cancellation reason codes, commercial interpretation of order statuses, buyer acknowledgement, audit retention/access/privacy, and geographic-location policy. The identity of the actor allowed to manually confirm a legacy-grade mapping is also unresolved; the prohibition on automatic mapping is not.

## 4. Phase 3.1 — Allocation data model and migration

### Scope

Design and migrate the persisted allocation proposal, version, line, and append-only event model. Add controlled-grade representation for allocation while preserving existing free-text grade values and preserving Phase 2 behavior.

### Prerequisites

- Approved Phase 2.1 policy.
- A documented manual-confirmation workflow for legacy-grade mappings and a production rollout/reversibility review. The approved policy is preservation plus no automatic backfill of ambiguous or unreviewed values.
- A migration/reversibility review for production PostgreSQL data.

### Expected files/modules

- `backend/app/models/allocation.py` (or focused proposal/line/event model modules)
- `backend/app/models/quality.py` if a controlled grade enum is isolated
- `backend/app/models/__init__.py`
- `backend/app/schemas.py` additions or allocation-specific schema module
- New Alembic revision(s) under `backend/alembic/versions/`
- Database schema/model tests under `backend/tests/`

### Implemented database changes

- `allocation_proposals`: UUID, status, creation/update timestamps, and optional ruleset version.
- `allocation_proposal_versions`: UUID, proposal foreign key, positive version number, timestamps, and unique `(proposal_id, version_number)` history identity.
- `allocation_lines`: UUID, proposal-version, batch, and order foreign keys, positive `NUMERIC(12,3)` proposed quantity, timestamps, and lookup indexes. The model supports split/partial line representation but performs no allocation, reservation, or lifecycle accounting.
- `allocation_events`: UUID, proposal reference, composite proposal-version/proposal reference, nonblank event type, optional JSON details, timestamps, restrictive foreign keys, and lookup indexes.
- Controlled allocation-grade enum/field(s), separate from raw `batches.quality_grade` and `orders.required_quality_grade`. Preserve the raw fields byte-for-byte for Phase 2. Existing rows start `Unknown/unmapped` unless an explicit manual confirmation has recorded a controlled-grade mapping; never bulk-classify ambiguous labels such as `Grade A` or `Export Quality`.
- Restrictive foreign keys and indexes for current proposal/version, line, event, status, batch, and order lookups.

### Deferred metadata and lifecycle fields

- Reservation-expiry timestamps and reservation state.
- Idempotency keys and request-correlation metadata.
- Trusted actor/approver references.
- Allocation-line lifecycle fields, validation snapshots, and event links to individual lines/batches/orders.
- Rich event before/after quantity snapshots, reasons, ruleset snapshots, and audit retention metadata.

These belong to later reservation, authorization, approval, and audit-workflow milestones; they are not represented by the Phase 3.1 schema.

### API changes

None exposed in this milestone. Internal schema/model support only.

### Business rules

- Existing free-text quality remains the Phase 2 source of truth for matching.
- Mapping failure, missing grade, ambiguous text, or unreviewed legacy text must be represented explicitly as `Unknown/unmapped`, not guessed. Mapping is manual-confirmation-only.
- The schema provides event-history storage and version identities; append-only enforcement and approved-history protection require later service/security controls.
- **Phase 3.1 limitation:** the data model records append-only history but does not itself prevent privileged direct SQL/ORM updates or deletes of versions/events. Unique version numbering is not immutability enforcement. Before operational workflows are enabled, a later service/security phase must restrict mutation paths, enforce authorization, and decide whether database privileges or triggers are required for immutable event/history protection.

### Test scenarios

- Upgrade/downgrade migration on an empty and populated test database.
- Foreign-key and deletion restrictions.
- Numeric precision/scale handling for `NUMERIC(12,3)` quantities.
- Event-history storage, composite proposal/version referential integrity, and line lookup indexes; append-only enforcement remains deferred.
- Legacy grade preservation and unmapped-grade handling.
- No automatic backfill for `Grade A`, `Export Quality`, similarly ambiguous values, or unreviewed rows.
- Phase 1 CRUD and Phase 2 matching regression tests over populated legacy-grade data.

### Acceptance criteria

- Alembic upgrades/downgrades cleanly in isolated test databases.
- Existing batch/order free-text grade values and all other Phase 1 data are preserved through upgrade and downgrade testing.
- Existing Phase 1 CRUD/schema tests and Phase 2 matching tests, including exact normalized free-text quality behavior, remain unchanged and passing against populated legacy data.
- No automatic legacy-grade backfill classifies ambiguous or unreviewed values; they remain `Unknown/unmapped` until manually confirmed.
- Phase 3.1 exposes no reservation behavior and no operational allocation API; it is limited to schema, migration, and regression-tested model support.
- New models can represent proposal history identities, proposed lines, controlled grades, and event-history records without changing Phase 1/2 records. Reservation, commitment, and immutable-history enforcement remain deferred.

### Explicitly out of scope

- Allocation generation, reservation writes, approval APIs, authentication, dispatch/delivery, and React allocation UI.

## 5. Phase 3.2 — Advisory draft generation and persistence

### Phase 3.2.1 — Deterministic advisory engine (completed)

### Scope

Create a pure deterministic service that generates advisory Draft results from an order and current batch records. The engine evaluates controlled allocation-grade compatibility independently of Phase 2's free-text matching and does not use Phase 2 candidate discovery or scoring. An advisory Draft has no reservation effect.

### Prerequisites

- Phase 3.1 proposal/line schema or an agreed in-memory draft representation wired to it.
- Approved quality compatibility matrix and deterministic tie-breaking policy.

### Expected files/modules

- `backend/app/allocation_engine.py`
- Allocation-specific domain types/schemas
- Focused allocation-engine unit tests
- Optional read-only internal query/repository helpers

### API changes

No operational write API. A later read-only draft-generation endpoint may be designed, but must clearly label output as advisory and must not mutate availability.

### Business rules

- Phase 2 matching remains independent; do not use its score or free-text quality matching as an allocation input, reservation, or approval.
- Enforce hard eligibility: normalized species, allocatable statuses, positive confirmed/available quantity, controlled quality matrix, known acceptable price, harvest/delivery/best-before conditions, and no aggregate overfulfillment.
- Apply approved split rules: one batch may serve many orders; one order may use many batches; partial amounts are allowed; unfulfilled remainder is visible; overfulfillment is prohibited.
- Rank deterministically using documented preferences and stable UUID tie-breakers. Location remains an exact-text soft preference only.
- Block automatic draft allocation when price, quality, or best-before data is unknown under the approved conservative policy.

### Test scenarios

- Exact eligible candidate, quality upgrade, prohibited downgrade, Processing restriction, Rejected/Unknown block.
- Unknown price/quality/best-before block without changing Phase 2 results.
- Multi-batch partial order and multi-order batch allocation.
- Stable ranking/tie-breaking and no overfulfillment.
- Draft generation does not write reservation/allocation/event state.

### Acceptance criteria

- Given the same persisted inputs and ruleset version, output is deterministic.
- Every draft exposes line quantities plus batch/order remaining quantities and unfulfilled demand.
- Existing matching behavior is unchanged and all Phase 2 tests pass.

### Explicitly out of scope

- Stock reservation, approval, cancellation, expiry, real route optimization, OR-Tools, LLMs, and frontend approval controls.

### Phase 3.2.2 — Advisory draft persistence

Persist a completed advisory engine result as one `DRAFT` proposal, version `1`, its proposed lines, and one `proposal_generated` event. Persistence owns a fresh SQLAlchemy session and one transaction, validates the current order and referenced batches, and records JSON-safe engine metadata. It does not rerun Phase 2 matching or replace the advisory engine's eligibility rules.

- Draft lines are checked against the current confirmed quantity, but are not reservations and do not deduct or otherwise alter stock or order quantities.
- The persistence transaction stores no temporary `batch_remaining_kg` value as authoritative availability.
- A persisted draft can become stale after it is written. Locking, availability accounting, reservations, idempotency, expiry, submission, and approval remain Phase 3.3+ responsibilities.
- A later submit/approve operation must revalidate authoritative current state under its own transaction and locking design.

## 6. Phase 3.3 — Quantity accounting and reservation service

### Scope

Implement authoritative server-side availability/order-remaining calculations and reservation lifecycle mechanics for submission, expiry, cancellation, and release preparation.

### Prerequisites

- Phase 3.1 persisted lines/events and migration.
- Phase 3.2 hard-rule evaluation reusable inside a transaction.
- Chosen expiry execution approach (scheduled worker, safe periodic service invocation, or a documented operational process).

### Expected files/modules

- `backend/app/services/quantity_accounting.py`
- `backend/app/services/reservation_service.py`
- Transaction/repository helpers for locked batches, orders, and active lines
- Expiry processor module/command with idempotent behavior
- Concurrency/integration tests

### API changes

Internal service API only until authentication exists. If a development-only submission endpoint is temporarily needed, it must be explicitly non-production and cannot claim coordinator authorization; preferred approach is no externally exposed write endpoint until 3.4.

### Business rules

- Available stock is calculated from persisted active reservation and committed allocation lines, never frontend state.
- Drafts do not reserve. Submission creates reservations with a configurable default 30-minute TTL.
- Rejection, cancellation, and expiry release exactly once. Expiry must transition state safely before availability is reused.
- Release applies only to remaining unconsumed committed quantity; dispatched/delivered accounting is out of scope.
- All quantities use Decimal/`NUMERIC(12,3)` arithmetic.

### Transaction and concurrency requirements

- Begin one PostgreSQL transaction for every allocation-changing operation.
- Lock affected batch and order rows in deterministic UUID order using `SELECT ... FOR UPDATE`.
- Read/lock active lines, recompute aggregate availability/remainder, then validate and write lines/events in the same transaction.
- Roll back all changes when any line fails. Use idempotency keys for writes and return retryable conflict details for lock/deadlock/serialization failures.

### Test scenarios

- Competing reservations against one batch cannot exceed confirmed quantity.
- Aggregate multi-line validation for same batch/order.
- TTL expiry versus submission/approval race yields one safe terminal state.
- Cancellation/rejection/expiry/release do not double-release.
- Batch correction below active commitments is rejected.
- Idempotent retries create one reservation/event sequence.
- Mid-transaction failure rolls back quantities, lines, and events.

### Acceptance criteria

- Invariants hold under concurrent integration tests.
- No available quantity becomes negative.
- Expired reservations are not counted as active after a successful expiry transition.
- All writes emit append-only events in the same committed transaction.

### Explicitly out of scope

- Production approval/release endpoint exposure, trusted actor handling, dispatch, delivery, and frontend operations controls.

## 7. Phase 3.4 — Authentication and authorization prerequisite

### Scope

Assess current security and design/implement genuine identity and coordinator authorization before operational approval writes are enabled.

### Prerequisites

- Product decision on identity provider/session model, user lifecycle, coordinator roles, and environment secret management.
- Threat model covering browser, API, service credentials, and audit access.

### Expected files/modules

- Security/auth configuration module(s)
- User/identity and role model/migration(s), if the approved architecture requires local identity records
- Authentication dependency/middleware and authorization policy module
- Trusted actor context for audit events
- Security and authorization test modules
- Development environment documentation

### API changes

- Auth/session/token endpoints or external identity integration as approved.
- Protected dependency/policy for submit, approve, reject, cancel, release, and override operations.
- No client-supplied role selector or actor ID is authoritative.

### Business rules

- A genuine authenticated identity provides the trusted actor ID for audit events.
- Authorization policy determines submit/approve/reject/cancel/release permissions.
- Approval thresholds, separation of duties, and override policy remain unresolved until confirmed.
- Development-only behavior must be documented; it cannot be labelled production authorization.

### Test scenarios

- Unauthenticated operations are denied.
- Unauthorized authenticated roles are denied.
- Authorized actor IDs are captured in audit events.
- Client-supplied actor/role fields cannot elevate privilege.
- Expired/revoked credentials and environment misconfiguration fail safely.

### Acceptance criteria

- Every operational allocation write can obtain a trusted actor and enforce policy server-side.
- No approval/release route relies on frontend role state.
- Security configuration and development limitations are documented.

### Explicitly out of scope

- Fake demo sign-in, trusting browser localStorage/session values, dispatch identity, and broad enterprise identity features not needed for allocation authorization.

## 8. Phase 3.5 — Coordinator approval and allocation APIs

### Scope

Expose authenticated APIs for proposal submission, approval, rejection, cancellation, release, revision, and audit history using all-or-nothing transaction semantics.

### Prerequisites

- Completed 3.1, 3.2, 3.3, and 3.4 acceptance criteria.
- Finalized authorization policy, reason-code requirements, and release reason definitions.

### Expected files/modules

- `backend/app/api/routes/allocations.py`
- Allocation Pydantic request/response schemas
- Allocation service orchestration layer
- Authorization dependencies
- Idempotency/conflict response helpers
- API and concurrency integration tests

### Proposed API changes

- `POST /allocation-proposals` — create/generate Draft.
- `GET /allocation-proposals` and `GET /allocation-proposals/{id}` — proposal, lines, derived quantities, and status.
- `PATCH /allocation-proposals/{id}` — Draft-only edit with optimistic versioning.
- `POST /allocation-proposals/{id}/submit` — authenticated submission/reservation.
- `POST /allocation-proposals/{id}/approve` — authenticated all-or-nothing approval/revalidation.
- `POST /allocation-proposals/{id}/reject`, `/cancel`, and `/release` — authenticated lifecycle changes with reasons.
- `POST /allocation-proposals/{id}/revisions` — immutable revision generation/resubmission.
- `GET /allocation-events` — authorized audit history query.

Every write requires an idempotency key and returns explicit validation/conflict state. These endpoint names remain proposed until API review.

### Business rules

- Revalidate all hard eligibility and aggregate quantities under lock at submit and approval.
- Pending reservation converts to committed allocation only after successful all-or-nothing approval.
- Any failed line causes complete rollback; no partial approval.
- Approved history remains immutable. Revisions are separate versions and events.
- Audit events remain append-only, with trusted actor identity from 3.4.

### Test scenarios

- Authorized happy paths for each lifecycle transition.
- Unauthenticated/unauthorized denial.
- Validation conflict after matching but before approval.
- Idempotency retry and concurrency conflict behavior.
- Reason/audit creation for rejection, cancellation, release, and revision.
- No mutation of Phase 2 matching results/records.

### Acceptance criteria

- Operational writes require authentication and authorization.
- Every lifecycle transition is transactional, idempotent, auditable, and concurrency-safe.
- Approval is all-or-nothing and approved history cannot be overwritten or deleted.

### Explicitly out of scope

- Dispatch execution, delivery proof, GPS/routing, buyer acceptance workflow unless separately approved, and any unauthenticated production approval endpoint.

## 9. Phase 3.6 — Tests and React coordinator workspace

### Scope

Build the coordinator-facing React review workflow only after authenticated allocation APIs exist, and expand automated regression, integration, concurrency, and responsive UI testing.

### Prerequisites

- Completed 3.5 authenticated APIs.
- Approved UX copy that clearly distinguishes Draft, Pending approval, Approved, Released, and historical state.
- A browser-test approach/tooling decision if end-to-end coverage is required.

### Expected files/modules

- `frontend/src/features/allocations/` feature pages, API client types, query/mutation hooks, and review components
- Proposal/line/audit table/card components consistent with existing shell
- Backend/frontend test additions and test fixtures
- Optional browser test configuration if approved

### API changes

Consumes 3.5 APIs only. No frontend-generated allocation accounting and no client-side authority. The UI must surface server conflicts, expiry, remaining quantities, and audit history.

### Business rules

- Draft review is advisory until submit.
- UI shows line details, pending reservations, batch/order remaining quantities, unfulfilled demand, quality/date/price blockers, and immutable history.
- Approval/rejection/release controls render only from trusted authenticated authorization state supplied by the backend; no fake role selector.
- Allocated, dispatched, and delivered labels remain distinct; dispatch/delivery screens are out of scope.

### Test scenarios

- Proposal generation/review and split/partial presentation.
- Pending reservation/TTL display and expired conflict handling.
- Approval/rejection/cancellation/release/revision flows with server conflict responses.
- Audit history rendering and immutable approved history.
- Responsive table/card/modal layout, keyboard navigation, focus management, loading/empty/error/retry states.
- Regression tests for Phase 1 CRUD and Phase 2 matching, including non-mutation.

### Acceptance criteria

- The coordinator workspace displays server-authoritative quantities and states.
- No UI action can claim approval without an authenticated/authorized API response.
- Responsive and accessibility regression checks pass, alongside backend transaction/concurrency tests and existing Phase 1/2 tests.

### Explicitly out of scope

- Actual dispatch, collection, delivery, replanning, agent telemetry, localStorage as authority, and standalone-demo allocation behavior.

## 10. Recommended first implementation task

Begin with **Phase 3.1 design refinement and migration implementation**: finalize the legacy free-text grade mapping strategy, create the allocation proposal/version/line/event schema, preserve raw grades, and add migration/schema regression tests. Do not expose allocation writes or modify Phase 2 matching during that work.
