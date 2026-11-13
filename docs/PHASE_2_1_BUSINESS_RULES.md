
# Phase 2.1 — Business Rules and Allocation Design

> **Status:** Finalized design. Approved decisions in this document define the intended Phase 3 policy; they do not change the Phase 1 CRUD APIs, the Phase 2 matching engine, database schema, or frontend behavior.
>
> Labels used below:
>
> - **Existing behavior** is verified from the current codebase.
> - **Proposed behavior** is a Phase 3 recommendation, not implemented behavior.
> - **Decision required** needs product/business confirmation before implementation.
> - **Approved** is a confirmed Phase 3 policy. It supersedes earlier proposed/default wording where applicable.

## Approved decision record

The following Phase 2.1 decisions are **APPROVED**:

1. **Quality compatibility:** Allocation uses Premium, Standard, Processing, and Rejected controlled grades. Premium can satisfy Premium or Standard; Standard only Standard; Processing only Processing; Rejected/Unknown never automatically allocate. No downgrade is permitted. Phase 2's exact normalized free-text matching remains unchanged.
2. **Partial and split allocation:** One order may use multiple batches; one batch may serve multiple orders; partial fulfillment is allowed; overfulfillment is prohibited; unfulfilled quantity stays visible.
3. **Reservation timing:** Drafts do not reserve. Submission creates reservations with a configurable, default 30-minute TTL. Approval revalidates. Rejection, cancellation, and expiry release each reservation exactly once.
4. **Approval policy:** Approval is all-or-nothing. Any failed line rolls back the entire approval transaction. Revision/resubmission creates history rather than silently overwriting approved proposals.
5. **Authentication and authorization:** Genuine coordinator authorization is deferred until a real authentication system exists. Client-supplied role identity is not trusted. Operational approval/release writes must not be presented or exposed as production-ready until authentication and authorization are implemented.
6. **Conservative automatic allocation and legacy grades:** Unknown asking price, best-before date, or quality blocks automatic allocation. Existing free-text grades are preserved and mapped to allocation grades only after explicit manual confirmation; ambiguous or unreviewed text remains `Unknown/unmapped`. The system must never infer that labels such as `Grade A` or `Export Quality` mean Premium or Standard.

## 1. Existing system behavior

### 1.1 Persisted domain and CRUD

**Existing behavior:** PostgreSQL has four persisted business tables: `growers`, `batches`, `buyers`, and `orders`. Each has a UUID primary key and server-generated `created_at` / `updated_at` timestamps.

- A batch belongs to one grower. It has `species`, `expected_kg`, optional `confirmed_kg`, `harvest_date`, optional `asking_price_per_kg`, optional free-text `quality_grade`, optional `best_before_date`, and one of `expected`, `confirmed`, `harvested`, or `cancelled`.
- An order belongs to one buyer. It has `species`, `quantity_kg`, `price_limit`, `delivery_date`, optional free-text `required_quality_grade`, and one of `pending`, `confirmed`, `fulfilled`, or `cancelled`.
- The database enforces non-negative expected/confirmed batch quantities and prices, non-negative order quantities/prices, nonblank required strings, and `best_before_date >= harvest_date` when a best-before date exists.
- Parent deletion is restricted if dependent batches or orders exist. CRUD requests use ordinary create/update/delete commits; there is no allocation-aware quantity validation.
- List filters exist for batch `grower_id`, `status`, and `species`; order `buyer_id`, `status`, and `species`; and grower/buyer `location`.

**Existing limitation:** There is no allocation, reservation, committed-quantity, quantity-ledger, proposal, event, approval, user, or coordinator model. `confirmed_kg` is not protected from being reduced below any future allocation because no allocation exists today.

### 1.2 Phase 2 matching

**Existing behavior:**

- `GET /matches/orders/{order_id}` ranks all batches for an eligible order.
- `GET /matches/batches/{batch_id}` ranks eligible orders for one batch.
- Both accept `minimum_score`, `species`, `eligible`, and `limit`.
- Eligible order statuses are `pending` and `confirmed`; eligible batch statuses are `confirmed` and `harvested`.
- Hard exclusions are normalized species mismatch, ineligible batch status, missing/non-positive `confirmed_kg`, harvest after delivery, known best-before before delivery, known asking price above price limit, and known unequal normalized quality grades.
- Known location differences are warnings, not exclusions. Missing best-before, price, quality, or location information is reported as unknown rather than automatically excluded.
- Score weights are quantity 30, freshness 25, price 20, quality 15, and exact normalized location 10. The score is normalized over known criteria. Species has weight zero but remains a hard exclusion.
- Partial quantity is eligible and receives partial quantity points. No quantity is reserved, deducted, allocated, or persisted by matching.

**Existing behavior that must remain unchanged in Phase 2:** Quality is exact normalized free-text equality when both values are known. Matching does not use a grade hierarchy. Location is exact normalized text only and has no distance/travel-time model.

### 1.3 Tests and React workflow inspected

**Existing behavior:** Backend tests cover CRUD validation, foreign-key restrictions, matching filters, ranked and excluded candidates, partial quantity, unknown data, inverse matching, and the absence of matching mutations. The React matching workspace selects an order or batch, passes the documented filters, and displays the backend response; it declares matching read-only.

## 2. Approved quality policy

The following controlled grades are **APPROVED** for **allocation**, not retroactively for Phase 2 matching:

- `Premium`
- `Standard`
- `Processing`
- `Rejected`

**Approved:** These grades are the Phase 3 allocation policy. Grade assignment process, actor responsibility, and any future “any acceptable grade” requirement remain operational details to define; they are not inferred as an industry standard.

### 2.1 Approved allocation compatibility matrix

Rows are batch grades. Columns are buyer requirements. `Allowed` means eligible without an override.

| Batch grade | Premium requirement | Standard requirement | Processing requirement |
|---|---|---|---|
| Premium | Allowed | Allowed | Not allowed |
| Standard | Not allowed | Allowed | Not allowed |
| Processing | Not allowed | Not allowed | Allowed |
| Rejected | Not allowed | Not allowed | Not allowed |
| Unknown/unmapped | Not allowed | Not allowed | Not allowed |

**Approved policy:** The policy permits the single clear upgrade (Premium for Standard), prohibits downgrades, treats Processing as purpose-restricted, and excludes Rejected and `Unknown/unmapped` material entirely from automatic allocation.

**Approved:** Premium and Standard are not automatically eligible for Processing demand. Any future buyer-specific substitution request must be introduced as an explicit, reviewed field; it is not hidden allocation logic.

### 2.2 Approved legacy-grade mapping policy

The original `batches.quality_grade` and `orders.required_quality_grade` free-text fields remain intact and remain Phase 2's source of truth. A separate controlled allocation-grade representation may be populated only after explicit manual confirmation. Missing, ambiguous, and unreviewed legacy values remain `Unknown/unmapped` and block automatic allocation. In particular, no code or migration may infer that text such as `Grade A`, `Export Quality`, or similar labels means Premium or Standard without explicit approval of that individual mapping.

### 2.3 Relationship to existing matching

**Existing behavior:** `premium`, `standard`, and any other free text are only exact normalized strings in matching; missing quality is unknown and can still be eligible.

**Approved Phase 3 behavior:** Allocation translates validated grades to a controlled enum and applies the matrix above as a hard rule. It must not alter historical Phase 2 match scores or their explanations.

## 3. Proposed allocation eligibility

Allocation must validate current database state at proposal submission and approval. A matching candidate is only a possible input; it is not an allocation authorization.

### 3.1 Hard eligibility (reject the allocation line)

| Condition | Proposed allocation treatment |
|---|---|
| Species | Exact normalized equality required. |
| Order status | Only `pending` or `confirmed`, pending a business decision on whether `confirmed` means commercially accepted. `fulfilled` and `cancelled` cannot receive new allocation. |
| Batch status | Only `confirmed` or `harvested`; `expected` and `cancelled` are ineligible. |
| Confirmed and available quantity | `confirmed_kg` must be positive and current calculated available quantity must cover at least some positive requested line quantity. |
| Quantity | Each allocation line must be positive; aggregate active lines may not exceed batch confirmed quantity or order remaining quantity. |
| Quality | Controlled grade must be known and compatible under the approved matrix. Rejected and unknown grades block automatic allocation. |
| Price | Batch asking price must be known and `<= order.price_limit`; unknown price blocks automatic allocation. |
| Dates | `harvest_date <= delivery_date`; best-before must be known and `delivery_date <= best_before_date`; unknown best-before blocks automatic allocation. |
| Referential integrity | Batch, grower, order, and buyer must still exist and be lockable in the approval transaction. |

**Approved conservative policy:** Unknown price blocks automatic allocation. Any future no-charge/internal-transfer exception requires an explicit, separately approved rule and audit semantics.

### 3.2 Soft preferences (rank, do not reject)

- Higher Phase 2 compatibility score and better known-data completeness.
- Greater order coverage while respecting all hard constraints.
- Earlier best-before date first, subject to delivery feasibility (a proposed freshness preference, not existing behavior).
- Fewer split lines and fewer distinct pickup sources, if commercial operations value simplicity.
- Exact normalized grower/buyer location match.
- Lower asking price within the buyer limit.

Location remains a soft preference until a trusted geocoding/routing service and business travel constraints are explicitly added. It must not be described as distance or route optimization.

### 3.3 Approved partial and split fulfillment

**Approved behavior:**

- A batch may serve multiple orders.
- An order may be served by multiple batches.
- Partial allocation is allowed and the unfilled remainder must remain visible.
- A proposal must expose `order_remaining_kg`, `batch_remaining_kg`, and any unallocated/unfulfilled quantities before approval.
- Overfulfillment is prohibited. The engine must not silently convert a partial allocation into `fulfilled` order status.

Buyer acknowledgement remains an unresolved operational process question; it does not change the approved split/partial/never-overfulfill rule.

## 4. Quantity accounting and invariants

### 4.1 Proposed lifecycle quantities

| Quantity | Definition | Source |
|---|---|---|
| Confirmed quantity | Physically confirmed batch quantity, `batches.confirmed_kg`. | Existing batch record. |
| Active reserved quantity | Sum of lines belonging to unexpired, pending-approval proposals. | Proposed allocation-line state. |
| Committed allocated quantity | Sum of approved, unreleased allocation lines. | Proposed allocation-line state. |
| Available quantity | `confirmed_kg - active_reserved_kg - committed_allocated_kg`. | Calculated in the transaction; optionally materialized only with strict synchronization. |
| Order remaining quantity | `order.quantity_kg - active_order_reservations - committed_order_allocations` (and, later, delivered quantity if dispatch is introduced). | Calculated from proposed lines. |

### 4.2 Invariants

For each batch:

```text
available_kg = confirmed_kg - active_reserved_kg - committed_allocated_kg
available_kg >= 0
active_reserved_kg + committed_allocated_kg <= confirmed_kg
```

For each order (approved policy):

```text
active_order_reservations_kg + committed_order_allocations_kg <= order.quantity_kg
```

The values must use `NUMERIC(12,3)`-compatible decimal arithmetic, never floating-point arithmetic.

### 4.3 Effects of changes

- **Partial allocation:** reserves/commits only the line quantity; the remainder remains visible.
- **Multiple lines:** aggregate all active lines by batch and by order before validating.
- **Order cancellation:** cancel/release pending reservations immediately in one transaction. Approved allocations require an explicit cancellation/release workflow and audit record; they must not disappear silently.
- **Reservation expiry:** atomically transition the proposal/lines out of active-reservation state before they can be considered available again.
- **Allocation release:** releases only undelivered/unconsumed committed quantity. Delivery/dispatch accounting is outside Phase 3 and needs a later explicit definition.
- **Batch confirmed-quantity correction:** lock the batch and all active lines. Reject a reduction below active reservations plus committed allocations. An increase becomes additional availability only after audit recording.

**Existing limitation:** No current field can record reserved or allocated quantity. Adding a `reserved_kg` column alone is insufficient because it cannot explain who reserved it, for which order, when it expires, or how it was approved.

## 5. Proposed allocation lifecycle

### 5.1 States and transition diagram

```text
Draft
  | submit (revalidate and reserve)
  v
Pending approval ---- expire ----> Expired
  | approve (convert reservation)      |
  v                                   | release reservation
Approved ---- release/cancel ----> Released / Cancelled
  |
  +---- cancellation requiring release/audit ----> Cancelled

Draft -------- reject/cancel --------> Rejected / Cancelled
Pending approval -- reject/cancel --> Rejected / Cancelled
```

Terminal states are `Rejected`, `Cancelled`, `Expired`, and `Released`. `Approved` is active until a defined later operational event releases or consumes the committed quantity.

### 5.2 Valid transitions and quantity effects

| From | To | Quantity effect |
|---|---|---|
| Draft | Pending approval | Revalidate and create active reservations. |
| Draft | Rejected / Cancelled | No reservation exists. |
| Pending approval | Approved | Convert each active reservation to committed allocation atomically; available quantity does not change at conversion. |
| Pending approval | Rejected / Cancelled / Expired | Release active reservation atomically. |
| Approved | Released | Release remaining committed quantity only; preserve historical committed quantity/event. |
| Approved | Cancelled | Explicitly release/cancel remaining committed quantity with reason and audit trail. |

**Approved reservation policy:** Draft generation is advisory and holds no stock. Submission for approval creates reservations. Pending reservations expire after a configurable TTL whose default is 30 minutes. Approval performs a fresh validation, not merely a state change. Rejection, cancellation, and expiry transition reservations safely and release each reservation exactly once before it is treated as available.

Pending-proposal edit-in-place behavior and release authority remain unresolved. The recommended revision model remains a new immutable proposal version rather than overwriting approved history.

### 5.3 Revision policy

**Approved behavior:** A revision creates a new immutable proposal version referencing the prior proposal. Do not edit approved lines in place. The original remains auditable; any still-active reservation is cancelled/released before its replacement becomes pending.

## 6. Coordinator approval workflow

1. Generate a **Draft** proposal from deterministic eligible candidates. It is advisory only.
2. Coordinator reviews lines, quantities, warnings, unknown data, and the explicit unfilled/order remainder.
3. Submit for approval. The backend locks affected rows, recalculates quantity/accounting, validates hard rules, and creates **Pending approval** reservations with expiry.
4. In a future authenticated deployment, an authorized coordinator approves or rejects with a required reason for rejection. Approval repeats validation under lock and converts reservations to committed allocations. Until genuine authentication/authorization exists, these are design-only operational writes and must not be exposed as production-ready actions.
5. The API returns the resulting immutable status/version and quantity summary. A failed validation returns conflict details; it must not partially approve lines.

**Approved prerequisite:** There is no authentication, role, or user model. Phase 3 cannot truthfully enforce “coordinator” authorization or record a meaningful actor until identity/authorization is supplied. Client-provided role selectors and actor IDs are not trustworthy and must not substitute for real authentication.

**Decision required:** Define coordinator roles, separation-of-duties requirements, approval thresholds, override authority, and required reason text for approval, rejection, cancellation, release, and quantity correction.

## 7. Concurrency and transaction strategy

Frontend checks are advisory only. The database transaction is the authoritative protection against overselling.

### 7.1 Proposed PostgreSQL workflow

For submit, approve, cancel, release, expiry processing, and confirmed-quantity correction:

1. Begin one database transaction.
2. Identify every affected batch and order.
3. Lock affected `batches` and `orders` rows with `SELECT ... FOR UPDATE`, in a deterministic UUID order to reduce deadlocks.
4. Lock/read all active reservation and committed allocation lines for those batch/order IDs.
5. Recalculate availability and order remainder from persisted lines inside that transaction.
6. Validate all hard eligibility and aggregate quantities.
7. Insert/update proposal lines and append audit events.
8. Commit once. On any validation error, deadlock, timeout, or serialization failure, roll back the entire operation.

`READ COMMITTED` plus explicit parent-row locks can be sufficient if every allocation-writing path takes the same locks. `SERIALIZABLE` provides stronger protection but requires bounded retry handling for serialization failures. The recommended initial implementation is explicit `FOR UPDATE` locking plus an idempotency key for submit/approve requests; evaluate `SERIALIZABLE` under load before production rollout.

### 7.2 Failure scenarios

- Two coordinators submit competing proposals: first transaction locks and reserves; second waits, recalculates, and either allocates a smaller valid amount or returns a conflict.
- Approval after expiry: lock and verify expiry before conversion; reject as expired, with no committed quantity created.
- One line invalid after another line is valid: reject/rollback the whole proposal unless the product explicitly supports line-level partial approval.
- Process crash before commit: PostgreSQL rollback leaves no reservation/approval artifact except independently committed prior events; events and state changes should be written in the same transaction.
- Duplicate client retry: idempotency key returns the original result rather than creating duplicate reservations or audit events.
- Lock deadlock/serialization failure: rollback and return a retryable conflict; never assume the frontend’s previous availability is current.

**Approved:** Approval is all-or-nothing. If any line fails current validation, the entire transaction rolls back. Line-level partial approval is out of scope unless separately approved.

## 8. Audit event specification

### 8.1 Proposed events

- `proposal_generated`
- `proposal_revised`
- `proposal_submitted`
- `reservation_created`
- `reservation_expired`
- `proposal_approved`
- `proposal_rejected`
- `proposal_cancelled`
- `allocation_released`
- `batch_quantity_corrected`
- `manual_override_applied` (only if approved as a policy)

### 8.2 Minimum immutable event fields

| Field | Purpose |
|---|---|
| Event UUID and occurred-at timestamp | Stable chronology. |
| Event type and schema version | Interpretability as rules evolve. |
| Proposal ID and version; line IDs where applicable | Correlates changes. |
| Batch IDs, order IDs, and quantity deltas | Reconstructs accounting. |
| Before/after status and before/after quantity snapshots | Explains state transition. |
| Actor ID, actor type, and authorization context | Accountability; unavailable until auth exists. |
| Reason code and free-text reason | Required for decisions/overrides/cancellation. |
| Rule evaluation snapshot | Species, quality, price, date, availability, score/version, and warnings used at decision time. |
| Correlation/idempotency/request ID | Diagnoses retries and distributed failures. |

Events should be append-only. Correcting an error creates a compensating event; it does not overwrite history.

## 9. Proposed database model changes

This is a proposed Phase 3 migration set, not an instruction to migrate now.

1. **Controlled quality representation**: Add a validated allocation-grade enum/field for new operational use, with a documented migration path from existing free-text `quality_grade` and `required_quality_grade`. Preserve existing raw values during transition.
2. **`allocation_proposals`**: UUID, status enum, version/revision parent, generated/submitted/approved/rejected/cancelled/expired timestamps, expiry timestamp, creator/approver IDs (nullable until auth exists), reasons, rule/version metadata, and idempotency key.
3. **`allocation_lines`**: UUID, proposal ID, batch ID, order ID, quantity `NUMERIC(12,3)`, line state, reservation/approval/release timestamps, and immutable validation snapshot. Index active lines by batch and order.
4. **`allocation_events`**: Append-only event table as specified above, indexed by proposal, batch, order, and timestamp.
5. **Optional future `order_fulfillment` / dispatch linkage**: Do not overload allocation lines to mean delivered quantity. Dispatch/delivery needs separate lifecycle data.

Prefer calculated availability from active lines first. A denormalized `reserved_kg` or `allocated_kg` batch counter may be added only later with transactionally enforced synchronization and reconciliation tests.

## 10. Proposed API design

Endpoint names are proposed and must be reviewed before implementation.

| Endpoint | Proposed purpose |
|---|---|
| `POST /allocation-proposals` | Generate/store a Draft from selected orders/batches and deterministic rule version; no reservation. |
| `GET /allocation-proposals` and `GET /allocation-proposals/{id}` | Query proposal, lines, status, quantity summary, warnings, and audit summary. |
| `PATCH /allocation-proposals/{id}` | Edit Draft lines only, using optimistic versioning. |
| `POST /allocation-proposals/{id}/submit` | Lock/revalidate and create Pending approval reservations. |
| `POST /allocation-proposals/{id}/approve` | Revalidate under lock and commit allocations. |
| `POST /allocation-proposals/{id}/reject` | Record reason and release reservation if present. |
| `POST /allocation-proposals/{id}/cancel` | Cancel Draft/Pending or explicitly cancel Approved remainder with reason. |
| `POST /allocation-proposals/{id}/release` | Release approved remaining quantity under a defined operational reason. |
| `GET /batches/{id}/availability` | Optional derived availability and active commitments; do not expose a mutable counter. |
| `GET /allocation-events` | Authorized audit query with proposal/batch/order filters. |

All write endpoints should support an idempotency key, return conflict details instead of silently changing quantities, and require an authenticated actor once identity exists.

## 11. Edge cases and failure scenarios

- A batch has confirmed quantity but no best-before date: existing matching may return it with unknown freshness; proposed allocation blocks it pending confirmation/override.
- An order/batch has missing grade: existing matching may return it with unknown quality; approved allocation policy blocks automatic allocation.
- Batch asking price is missing: existing matching may return unknown price; approved allocation policy blocks automatic allocation.
- Batch status changes to cancelled between matching and approval: approval transaction rejects the line.
- Order is cancelled while pending: release reservation and record event atomically.
- Confirmed quantity is reduced after a pending/approved proposal: block reduction below commitments; require correction reason and revalidation.
- Multiple lines refer to the same batch/order inside one proposal: aggregate before validation.
- Buyer order was already partially approved by another proposal: calculate remaining order quantity under lock.
- Same request is retried after a timeout: idempotency key avoids duplicate reservations.
- Expiry worker races with approval: lock the proposal and lines; only one transition wins.
- Existing free-text grades cannot map to a controlled grade: retain raw text, mark unmapped, and block allocation until corrected or explicitly overridden.

## 12. Migration risks

- Existing data may have null or arbitrary quality grades; automatic mapping to the proposed enum can be incorrect.
- Existing `confirmed_kg` can be zero or null, and current CRUD permits corrections with no allocation awareness.
- Existing order/batch status meaning is not documented as a commercial approval/delivery contract.
- Adding allocations changes deletion and update behavior: parent restrictions and correction rules must be designed before migration.
- Introducing reservation TTL requires a reliable worker/scheduler or an on-read/on-write expiry mechanism, plus idempotent processing.
- No current authentication means actor/audit semantics are incomplete and coordinator authorization cannot be enforced.
- Backfilling availability counters risks drift; calculated availability is safer initially.

## 13. Phase 3 test scenarios

1. Draft generation is read-only and does not change availability.
2. Submit reserves exactly the aggregate line quantity and never exceeds confirmed batch quantity.
3. Two concurrent submissions for the same batch cannot over-reserve.
4. Approval converts a valid reservation to a committed allocation without double-counting availability.
5. Rejection, cancellation, release, and expiry return quantity exactly once.
6. Multiple batches can partially satisfy one order; multiple orders can consume one batch within aggregate limits.
7. Quality matrix allows the approved Premium-to-Standard upgrade only and rejects all disallowed combinations, Rejected material, and Unknown/unmapped grades.
8. Unknown/missing quality, price, and best-before data block automatic allocation under the approved policy while Phase 2 matching remains unchanged.
9. Price/date/species/status failures reject atomically with useful line-level conflict explanations.
10. Batch confirmed-quantity reduction below active commitments fails; valid increases/reductions are audited.
11. Order cancellation releases pending reservation and handles approved allocation under the defined policy.
12. Expiry/approval race yields one terminal result and a complete audit trail.
13. Duplicate idempotency requests create one proposal/reservation/event sequence.
14. Rollback after a mid-transaction validation failure leaves no partial lines/events/quantity changes.
15. CRUD/matching regression tests still pass and matching remains non-mutating.
16. API authorization tests verify only authorized coordinators can submit/approve/release/override once identity exists.

## 14. Decision status and unresolved operational details

### Resolved / APPROVED

1. Controlled grades and the Premium/Standard/Processing/Rejected compatibility matrix.
2. Premium-to-Standard upgrade only; no downgrade; Rejected/Unknown automatic-allocation block.
3. Split/partial allocation, visible unfulfilled quantity, and no overfulfillment.
4. Draft-no-reservation; submission reservation; configurable default 30-minute TTL; revalidation; exactly-once release on rejection/cancellation/expiry.
5. All-or-nothing approval, revision/resubmission, and no silent overwrite of approved history.
6. Authentication/authorization is a prerequisite for production-ready operational approval/release writes; no fake client role identity.
7. Decimal `NUMERIC(12,3)`-compatible accounting, calculated availability from persisted active reservation/committed allocation lines, append-only events, row locking, expiry transition safety, and idempotency keys.
8. Unknown price, quality, and best-before data block automatic allocation. Legacy free-text grades are preserved, require explicit manual confirmation before controlled-grade mapping, and otherwise remain `Unknown/unmapped`; ambiguous labels such as `Grade A` and `Export Quality` are never auto-classified.

### Unresolved (do not infer answers)

1. Which trusted actors may manually confirm legacy-grade mappings; the no-auto-mapping policy is already approved.
2. Whether a separately approved override process may ever permit unknown quality, price, or freshness, and its required evidence/reasons.
3. Commercial meaning of `pending`, `confirmed`, and `fulfilled` order states before dispatch/delivery exists.
4. Whether buyer acknowledgement is required before or after coordinator approval.
5. Trusted actor roles, approval thresholds, separation of duties, and reason-code requirements once authentication exists.
6. Who may cancel/release/correct approved allocations and which release reasons are allowed.
7. Operational meaning of unconsumed committed quantity and its transition to dispatched/delivered quantities in a later phase.
8. Geographic location policy: retain exact-text preference or adopt verified geographic constraints later.
9. Audit-event retention, privacy, and access-control requirements.

## 15. Recommended Phase 3 implementation order

After the decisions above are approved: implement a controlled-grade migration that preserves raw values and leaves every unconfirmed mapping `Unknown/unmapped`; add allocation proposal/line/event schema and transaction services; implement locking/idempotency/expiry; expose reviewed APIs only after authentication/authorization; add concurrency and audit tests; then build a coordinator review UI. Do not use Phase 2 matching output as a reservation or allocation command without the Phase 3 transaction boundary.
