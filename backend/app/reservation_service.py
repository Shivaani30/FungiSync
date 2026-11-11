"""Transactionally consistent allocation proposal capacity and submission services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.allocation_engine import _batch_exclusions, _order_exclusions
from app.core.config import get_settings
from app.models import (
    AllocationEvent,
    AllocationLine,
    AllocationOperationIdempotency,
    AllocationOperationIdempotencyState,
    AllocationProposal,
    AllocationProposalStatus,
    AllocationProposalVersion,
    AllocationReservation,
    AllocationReservationStatus,
    Batch,
    Order,
)
from app.quantity_accounting import batch_available_kg, order_remaining_kg
from app.reservation_transaction import (
    effective_timestamp,
    lock_allocation_lines,
    lock_reservation_targets,
    MissingLockedRecordsError,
    reread_locked_targets,
    run_reservation_transaction,
    is_retryable_postgres_failure,
)


ZERO = Decimal("0")


class ProposalNotFoundError(ValueError):
    """The requested proposal does not exist."""


class InvalidProposalVersionError(ValueError):
    """The proposal has no usable current version."""


class MissingReferencedRecordError(ValueError):
    """A line refers to a batch or order missing from the locked target set."""


class InvalidAllocationLineError(ValueError):
    """A persisted allocation line cannot safely be used for capacity validation."""


class ProposalTargetsChangedError(ValueError):
    """The post-lock proposal/reservation target set differs from the discovered set."""


class ProposalNotSubmittableError(ValueError):
    """The proposal is not a DRAFT and therefore cannot be submitted."""


class ProposalNotAwaitingApprovalError(ValueError):
    """The proposal is not in PENDING_APPROVAL and cannot be approved."""


class MissingProposalReservationsError(ValueError):
    """The proposal version does not have exactly one reservation per allocation line."""


class ExpiredReservationError(ValueError):
    """A pending reservation reached its expiry before approval."""


class InvalidReservationStateError(ValueError):
    """A proposal reservation is not pending and cannot be committed."""


class InvalidReservationError(ValueError):
    """A reservation no longer matches the proposal version and allocation-line snapshot."""


class InvalidTerminalReasonError(ValueError):
    """The supplied terminal reason fields are invalid."""


class ProposalNotTerminableError(ValueError):
    """The proposal state does not permit the requested terminal transition."""


class IdempotencyConflictError(ValueError):
    """A release key was previously used for different inputs."""


class IneligibleReferencedRecordError(ValueError):
    """A referenced batch or order fails the existing allocation eligibility rules."""


class InsufficientBatchCapacityError(ValueError):
    """The locked batch capacity cannot cover the submitted allocation."""


class InsufficientOrderCapacityError(ValueError):
    """The locked order capacity cannot cover the submitted allocation."""


class RetryableReservationTransactionError(RuntimeError):
    """A PostgreSQL serialization, deadlock, or lock failure requires caller retry."""


@dataclass(frozen=True)
class BatchCapacity:
    batch_id: UUID
    proposed_quantity_kg: Decimal
    available_quantity_kg: Decimal


@dataclass(frozen=True)
class OrderCapacity:
    order_id: UUID
    proposed_quantity_kg: Decimal
    remaining_quantity_kg: Decimal


@dataclass(frozen=True)
class CapacityConflict:
    resource_type: str
    resource_id: UUID
    proposed_quantity_kg: Decimal
    available_quantity_kg: Decimal


@dataclass(frozen=True)
class ProposalCapacityValidation:
    proposal_id: UUID
    proposal_version_id: UUID
    capacity_valid: bool
    effective_at: datetime
    batches: tuple[BatchCapacity, ...]
    orders: tuple[OrderCapacity, ...]
    conflicts: tuple[CapacityConflict, ...]


@dataclass(frozen=True)
class SubmittedProposal:
    """Committed result of submitting one persisted DRAFT proposal."""

    proposal_id: UUID
    proposal_version_id: UUID
    reservation_ids: tuple[UUID, ...]
    event_id: UUID
    effective_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ApprovedProposal:
    """Committed result of approving all reservations for one proposal version."""

    proposal_id: UUID
    proposal_version_id: UUID
    reservation_ids: tuple[UUID, ...]
    event_id: UUID
    effective_at: datetime


@dataclass(frozen=True)
class TerminalProposal:
    proposal_id: UUID
    proposal_version_id: UUID
    reservation_ids: tuple[UUID, ...]
    event_id: UUID
    effective_at: datetime
    status: AllocationProposalStatus


def _release_fingerprint(proposal_id: UUID, code: str, explanation: str | None) -> str:
    payload = json.dumps([str(proposal_id), code, explanation], separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _stored_release_result(operation: AllocationOperationIdempotency) -> TerminalProposal:
    result = operation.result_json
    if (operation.state is not AllocationOperationIdempotencyState.COMPLETED
            or operation.result_event_id is None or operation.proposal_version_id is None
            or not isinstance(result, dict)):
        raise IdempotencyConflictError("Release key has no completed result")
    try:
        if result["status"] != AllocationProposalStatus.RELEASED.value:
            raise ValueError("Unexpected stored release status")
        proposal_id = UUID(result["proposal_id"])
        proposal_version_id = UUID(result["proposal_version_id"])
        reservation_ids = tuple(UUID(value) for value in result["reservation_ids"])
        effective_at = datetime.fromisoformat(result["effective_at"])
        event = operation.result_event
        if (proposal_id != operation.proposal_id or proposal_version_id != operation.proposal_version_id
                or not reservation_ids or effective_at.tzinfo is None or effective_at.utcoffset() is None
                or event is None or event.id != operation.result_event_id
                or event.event_type != "proposal_released" or event.proposal_id != proposal_id
                or event.proposal_version_id != proposal_version_id):
            raise ValueError("Stored release result does not match its operation and event")
        return TerminalProposal(
            proposal_id=proposal_id,
            proposal_version_id=proposal_version_id,
            reservation_ids=reservation_ids,
            event_id=operation.result_event_id,
            effective_at=effective_at,
            status=AllocationProposalStatus.RELEASED,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IdempotencyConflictError("Release key has an invalid stored result") from error


def _current_version(session: Session, proposal_id: UUID) -> AllocationProposalVersion:
    version = session.scalars(
        select(AllocationProposalVersion)
        .where(AllocationProposalVersion.proposal_id == proposal_id)
        .order_by(AllocationProposalVersion.version_number.desc(), AllocationProposalVersion.id.asc())
    ).first()
    if version is None:
        raise InvalidProposalVersionError("Proposal has no allocation proposal version")
    return version


def _lines_for_version(session: Session, version_id: UUID) -> tuple[AllocationLine, ...]:
    return tuple(session.scalars(
        select(AllocationLine)
        .where(AllocationLine.proposal_version_id == version_id)
        .order_by(AllocationLine.id.asc())
        .execution_options(populate_existing=True)
    ))


def _line_fingerprint(lines: tuple[AllocationLine, ...], version_id: UUID) -> tuple[tuple[UUID, UUID, UUID, Decimal], ...]:
    fingerprint: list[tuple[UUID, UUID, UUID, Decimal]] = []
    for line in lines:
        if not isinstance(line, AllocationLine):
            raise InvalidAllocationLineError("Proposal version contains an invalid allocation line")
        if line.proposal_version_id != version_id:
            raise InvalidAllocationLineError("Allocation line does not belong to the selected version")
        if not all(isinstance(value, UUID) for value in (line.id, line.batch_id, line.order_id)):
            raise InvalidAllocationLineError("Allocation line identifiers must be UUID values")
        quantity = line.proposed_quantity_kg
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= ZERO:
            raise InvalidAllocationLineError("Allocation line proposed_quantity_kg must be a positive finite Decimal")
        fingerprint.append((line.id, line.batch_id, line.order_id, quantity))
    return tuple(fingerprint)


def _reservation_ids_for_targets(session: Session, batch_ids: tuple[UUID, ...], order_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if not batch_ids and not order_ids:
        return ()
    predicates = []
    if batch_ids:
        predicates.append(AllocationLine.batch_id.in_(batch_ids))
    if order_ids:
        predicates.append(AllocationLine.order_id.in_(order_ids))
    return tuple(session.scalars(
        select(AllocationReservation.id)
        .join(AllocationLine, AllocationReservation.allocation_line_id == AllocationLine.id)
        .where(or_(*predicates))
        .order_by(AllocationReservation.id.asc())
    ))


def _reservation_ids_for_proposal_version(
    session: Session,
    proposal_id: UUID,
    proposal_version_id: UUID,
) -> tuple[UUID, ...]:
    return tuple(session.scalars(
        select(AllocationReservation.id)
        .where(
            AllocationReservation.proposal_id == proposal_id,
            AllocationReservation.proposal_version_id == proposal_version_id,
        )
        .order_by(AllocationReservation.id.asc())
    ))


def _reservations_with_lines(session: Session, reservation_ids: tuple[UUID, ...]) -> tuple[AllocationReservation, ...]:
    if not reservation_ids:
        return ()
    return tuple(session.scalars(
        select(AllocationReservation)
        .where(AllocationReservation.id.in_(reservation_ids))
        .order_by(AllocationReservation.id.asc())
        .options(selectinload(AllocationReservation.allocation_line))
        .execution_options(populate_existing=True)
    ))


def _aggregate_lines(lines: tuple[AllocationLine, ...], attribute: str) -> dict[UUID, Decimal]:
    quantities: dict[UUID, Decimal] = {}
    for line in lines:
        target_id = getattr(line, attribute)
        quantities[target_id] = quantities.get(target_id, ZERO) + line.proposed_quantity_kg
    return quantities


def _records_by_id(records: tuple[Batch, ...] | tuple[Order, ...], ids: tuple[UUID, ...], kind: str) -> dict[UUID, Batch | Order]:
    mapping = {record.id: record for record in records}
    missing = set(ids) - mapping.keys()
    if missing:
        raise MissingReferencedRecordError(f"Proposal references missing {kind} records")
    return mapping


def _ensure_submission_eligibility(
    batches: dict[UUID, Batch],
    orders: dict[UUID, Order],
    lines: tuple[AllocationLine, ...],
) -> None:
    """Apply the same persisted-record eligibility rules used by draft generation."""
    for order in orders.values():
        if _order_exclusions(order):
            raise IneligibleReferencedRecordError("Referenced order is not allocation-eligible")
    for line in lines:
        batch = batches[line.batch_id]
        order = orders[line.order_id]
        if _batch_exclusions(order, batch):
            raise IneligibleReferencedRecordError("Referenced batch is not allocation-eligible for its order")


def _submission_capacity_errors(
    *,
    batches: dict[UUID, Batch],
    orders: dict[UUID, Order],
    lines: tuple[AllocationLine, ...],
    reservations: tuple[AllocationReservation, ...],
    effective_at: datetime,
) -> None:
    batch_proposed = _aggregate_lines(lines, "batch_id")
    order_proposed = _aggregate_lines(lines, "order_id")
    for batch_id in sorted(batch_proposed):
        available = batch_available_kg(batches[batch_id], reservations, effective_at=effective_at)
        if batch_proposed[batch_id] > available:
            raise InsufficientBatchCapacityError("Referenced batch has insufficient available capacity")
    for order_id in sorted(order_proposed):
        remaining = order_remaining_kg(orders[order_id], reservations, effective_at=effective_at)
        if order_proposed[order_id] > remaining:
            raise InsufficientOrderCapacityError("Referenced order has insufficient remaining capacity")


def submit_allocation_proposal(
    session_factory: sessionmaker[Session],
    proposal_id: UUID,
) -> SubmittedProposal:
    """Atomically submit a DRAFT proposal and create one PENDING reservation per line.

    This intentionally accepts no idempotency key: it is an internal operation,
    and the proposal row lock serializes concurrent submissions of the same
    proposal.  A repeat after commit is rejected because the proposal is no
    longer DRAFT; no expiry is renewed.
    """
    if not isinstance(proposal_id, UUID):
        raise ValueError("proposal_id must be a UUID")
    ttl_seconds = get_settings().reservation_pending_ttl_seconds

    def operation(session: Session) -> SubmittedProposal:
        proposal = session.get(AllocationProposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError("Allocation proposal does not exist")
        discovered_version = _current_version(session, proposal_id)
        discovered_lines = _lines_for_version(session, discovered_version.id)
        discovered_fingerprint = _line_fingerprint(discovered_lines, discovered_version.id)
        line_ids = tuple(line.id for line in discovered_lines)
        batch_ids = tuple(sorted({line.batch_id for line in discovered_lines}))
        order_ids = tuple(sorted({line.order_id for line in discovered_lines}))

        lock_reservation_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
        )
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise ProposalTargetsChangedError("Allocation line lock target set changed")
        current_version = _current_version(session, proposal_id)
        current_lines = _lines_for_version(session, current_version.id)
        if current_version.id != discovered_version.id or _line_fingerprint(current_lines, current_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed while acquiring parent locks")
        locked_batch_ids = tuple(sorted({line.batch_id for line in locked_lines}))
        locked_order_ids = tuple(sorted({line.order_id for line in locked_lines}))
        if locked_batch_ids != batch_ids or locked_order_ids != order_ids:
            raise ProposalTargetsChangedError("Allocation line targets changed while acquiring line locks")

        reservation_ids = _reservation_ids_for_targets(session, locked_batch_ids, locked_order_ids)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
            reservation_ids=reservation_ids,
        )
        locked_proposal = reread.proposals[0]
        if locked_proposal.status is not AllocationProposalStatus.DRAFT:
            raise ProposalNotSubmittableError("Allocation proposal is not in DRAFT state")
        final_version = _current_version(session, proposal_id)
        final_lines = _lines_for_version(session, final_version.id)
        if final_version.id != discovered_version.id or _line_fingerprint(final_lines, final_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed after locking")
        if not final_lines:
            raise InvalidAllocationLineError("Allocation proposal cannot be submitted without allocation lines")
        final_reservation_ids = _reservation_ids_for_targets(session, batch_ids, order_ids)
        if final_reservation_ids != reservation_ids:
            raise ProposalTargetsChangedError("Relevant reservation set changed while acquiring reservation locks")
        if {reservation.allocation_line_id for reservation in reread.reservations}.intersection(line_ids):
            raise InvalidAllocationLineError("Allocation line already has a reservation")

        batches = _records_by_id(reread.batches, batch_ids, "batch")
        orders = _records_by_id(reread.orders, order_ids, "order")
        reservations = _reservations_with_lines(session, reservation_ids)
        if tuple(reservation.id for reservation in reservations) != reservation_ids:
            raise ProposalTargetsChangedError("Locked reservation records could not be re-read")
        _ensure_submission_eligibility(batches, orders, final_lines)
        effective_at = effective_timestamp(session)
        _submission_capacity_errors(
            batches=batches,
            orders=orders,
            lines=final_lines,
            reservations=reservations,
            effective_at=effective_at,
        )
        expires_at = effective_at + timedelta(seconds=ttl_seconds)
        created = [
            AllocationReservation(
                allocation_line_id=line.id,
                proposal_id=proposal_id,
                proposal_version_id=final_version.id,
                reserved_quantity_kg=line.proposed_quantity_kg,
                status=AllocationReservationStatus.PENDING,
                expires_at=expires_at,
            )
            for line in final_lines
        ]
        session.add_all(created)
        session.flush()
        locked_proposal.status = AllocationProposalStatus.PENDING_APPROVAL
        locked_proposal.submitted_at = effective_at
        locked_proposal.expires_at = expires_at
        event = AllocationEvent(
            proposal_id=proposal_id,
            proposal_version_id=final_version.id,
            event_type="proposal_submitted",
            details={"reservation_count": len(created), "expires_at": expires_at.isoformat()},
        )
        session.add(event)
        session.flush()
        return SubmittedProposal(
            proposal_id=proposal_id,
            proposal_version_id=final_version.id,
            reservation_ids=tuple(reservation.id for reservation in created),
            event_id=event.id,
            effective_at=effective_at,
            expires_at=expires_at,
        )

    try:
        return run_reservation_transaction(session_factory, operation)
    except MissingLockedRecordsError as error:
        raise MissingReferencedRecordError("Required submission target no longer exists") from error
    except DBAPIError as error:
        if is_retryable_postgres_failure(error):
            raise RetryableReservationTransactionError("Retryable PostgreSQL reservation transaction failure") from error
        raise


def approve_allocation_proposal(
    session_factory: sessionmaker[Session],
    proposal_id: UUID,
) -> ApprovedProposal:
    """Atomically commit every unexpired PENDING reservation for a submitted proposal.

    This internal operation has no request idempotency key because the repository
    has no service-level key/fingerprint convention.  The proposal lock serializes
    concurrent calls; a completed approval is no longer PENDING_APPROVAL.
    """
    if not isinstance(proposal_id, UUID):
        raise ValueError("proposal_id must be a UUID")

    def operation(session: Session) -> ApprovedProposal:
        proposal = session.get(AllocationProposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError("Allocation proposal does not exist")
        discovered_version = _current_version(session, proposal_id)
        discovered_lines = _lines_for_version(session, discovered_version.id)
        discovered_fingerprint = _line_fingerprint(discovered_lines, discovered_version.id)
        line_ids = tuple(line.id for line in discovered_lines)
        batch_ids = tuple(sorted({line.batch_id for line in discovered_lines}))
        order_ids = tuple(sorted({line.order_id for line in discovered_lines}))

        lock_reservation_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
        )
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise ProposalTargetsChangedError("Allocation line lock target set changed")
        current_version = _current_version(session, proposal_id)
        current_lines = _lines_for_version(session, current_version.id)
        if current_version.id != discovered_version.id or _line_fingerprint(current_lines, current_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed while acquiring parent locks")
        if tuple(sorted({line.batch_id for line in locked_lines})) != batch_ids or tuple(sorted({line.order_id for line in locked_lines})) != order_ids:
            raise ProposalTargetsChangedError("Allocation line targets changed while acquiring line locks")

        reservation_ids = _reservation_ids_for_targets(session, batch_ids, order_ids)
        proposal_reservation_ids = _reservation_ids_for_proposal_version(session, proposal_id, discovered_version.id)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
            reservation_ids=reservation_ids,
        )
        locked_proposal = reread.proposals[0]
        if locked_proposal.status is not AllocationProposalStatus.PENDING_APPROVAL:
            raise ProposalNotAwaitingApprovalError("Allocation proposal is not awaiting approval")
        final_version = _current_version(session, proposal_id)
        final_lines = _lines_for_version(session, final_version.id)
        if final_version.id != discovered_version.id or _line_fingerprint(final_lines, final_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed after locking")
        if _reservation_ids_for_targets(session, batch_ids, order_ids) != reservation_ids:
            raise ProposalTargetsChangedError("Relevant reservation set changed while acquiring reservation locks")
        if _reservation_ids_for_proposal_version(session, proposal_id, final_version.id) != proposal_reservation_ids:
            raise ProposalTargetsChangedError("Proposal reservation set changed while acquiring reservation locks")

        _records_by_id(reread.batches, batch_ids, "batch")
        _records_by_id(reread.orders, order_ids, "order")
        reservations = _reservations_with_lines(session, reservation_ids)
        if tuple(reservation.id for reservation in reservations) != reservation_ids:
            raise ProposalTargetsChangedError("Locked reservation records could not be re-read")
        proposal_reservations = tuple(
            reservation for reservation in reservations
            if reservation.id in set(proposal_reservation_ids)
        )
        if not proposal_reservations or {reservation.allocation_line_id for reservation in proposal_reservations} != set(line_ids):
            raise MissingProposalReservationsError("Proposal version must have exactly one reservation per allocation line")
        if len(proposal_reservations) != len(line_ids):
            raise MissingProposalReservationsError("Proposal version has duplicate or missing reservations")

        effective_at = effective_timestamp(session)
        proposal_expires_at = locked_proposal.expires_at
        if proposal_expires_at is None or proposal_expires_at <= effective_at:
            raise ExpiredReservationError("Allocation proposal has expired")
        lines_by_id = {line.id: line for line in final_lines}
        for reservation in proposal_reservations:
            line = reservation.allocation_line
            if line is None or line.id not in lines_by_id:
                raise InvalidReservationError("Reservation allocation line is missing from the proposal version")
            if (reservation.proposal_id != proposal_id or reservation.proposal_version_id != final_version.id
                    or line.proposal_version_id != final_version.id
                    or reservation.reserved_quantity_kg != line.proposed_quantity_kg):
                raise InvalidReservationError("Reservation snapshot does not match its allocation line")
            if reservation.status is not AllocationReservationStatus.PENDING:
                raise InvalidReservationStateError("Proposal reservation is not PENDING")
            if reservation.expires_at is None or reservation.expires_at <= effective_at:
                raise ExpiredReservationError("Proposal reservation has expired")
            if reservation.expires_at != proposal_expires_at:
                raise InvalidReservationError("Reservation expiry does not match the proposal expiry")

        for reservation in proposal_reservations:
            reservation.status = AllocationReservationStatus.COMMITTED
            reservation.committed_at = effective_at
        locked_proposal.status = AllocationProposalStatus.APPROVED
        locked_proposal.approved_at = effective_at
        event = AllocationEvent(
            proposal_id=proposal_id,
            proposal_version_id=final_version.id,
            event_type="proposal_approved",
            details={"reservation_count": len(proposal_reservations)},
        )
        session.add(event)
        session.flush()
        return ApprovedProposal(
            proposal_id=proposal_id,
            proposal_version_id=final_version.id,
            reservation_ids=tuple(reservation.id for reservation in proposal_reservations),
            event_id=event.id,
            effective_at=effective_at,
        )

    try:
        return run_reservation_transaction(session_factory, operation)
    except MissingLockedRecordsError as error:
        raise MissingReferencedRecordError("Required approval target no longer exists") from error
    except DBAPIError as error:
        if is_retryable_postgres_failure(error):
            raise RetryableReservationTransactionError("Retryable PostgreSQL reservation transaction failure") from error
        raise


def _terminal_reason(reason_code: str, reason: str | None) -> tuple[str, str | None]:
    if not isinstance(reason_code, str) or not reason_code.strip() or len(reason_code) > 100:
        raise InvalidTerminalReasonError("reason_code must be a nonblank string of at most 100 characters")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise InvalidTerminalReasonError("reason must be a nonblank string when supplied")
    return reason_code.strip(), reason.strip() if reason is not None else None


def _terminate_allocation_proposal(
    session_factory: sessionmaker[Session],
    proposal_id: UUID,
    *,
    reason_code: str,
    reason: str | None,
    rejection: bool,
) -> TerminalProposal:
    if not isinstance(proposal_id, UUID):
        raise ValueError("proposal_id must be a UUID")
    code, explanation = _terminal_reason(reason_code, reason)

    def operation(session: Session) -> TerminalProposal:
        if session.get(AllocationProposal, proposal_id) is None:
            raise ProposalNotFoundError("Allocation proposal does not exist")
        discovered_version = _current_version(session, proposal_id)
        discovered_lines = _lines_for_version(session, discovered_version.id)
        discovered_fingerprint = _line_fingerprint(discovered_lines, discovered_version.id)
        line_ids = tuple(line.id for line in discovered_lines)
        batch_ids = tuple(sorted({line.batch_id for line in discovered_lines}))
        order_ids = tuple(sorted({line.order_id for line in discovered_lines}))

        lock_reservation_targets(session, batch_ids=batch_ids, order_ids=order_ids, proposal_ids=(proposal_id,))
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise ProposalTargetsChangedError("Allocation line lock target set changed")
        current_version = _current_version(session, proposal_id)
        current_lines = _lines_for_version(session, current_version.id)
        if current_version.id != discovered_version.id or _line_fingerprint(current_lines, current_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed while acquiring parent locks")
        if tuple(sorted({line.batch_id for line in locked_lines})) != batch_ids or tuple(sorted({line.order_id for line in locked_lines})) != order_ids:
            raise ProposalTargetsChangedError("Allocation line targets changed while acquiring line locks")

        reservation_ids = _reservation_ids_for_targets(session, batch_ids, order_ids)
        proposal_reservation_ids = _reservation_ids_for_proposal_version(session, proposal_id, discovered_version.id)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(session, batch_ids=batch_ids, order_ids=order_ids, proposal_ids=(proposal_id,), reservation_ids=reservation_ids)
        locked_proposal = reread.proposals[0]
        source_status = locked_proposal.status
        if rejection and source_status is not AllocationProposalStatus.PENDING_APPROVAL:
            raise ProposalNotTerminableError("Only a proposal awaiting approval may be rejected")
        if not rejection and source_status not in (AllocationProposalStatus.PENDING_APPROVAL, AllocationProposalStatus.APPROVED):
            raise ProposalNotTerminableError("Proposal cannot be cancelled from its current state")
        final_version = _current_version(session, proposal_id)
        final_lines = _lines_for_version(session, final_version.id)
        if final_version.id != discovered_version.id or _line_fingerprint(final_lines, final_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed after locking")
        if _reservation_ids_for_targets(session, batch_ids, order_ids) != reservation_ids:
            raise ProposalTargetsChangedError("Relevant reservation set changed while acquiring reservation locks")
        if _reservation_ids_for_proposal_version(session, proposal_id, final_version.id) != proposal_reservation_ids:
            raise ProposalTargetsChangedError("Proposal reservation set changed while acquiring reservation locks")
        _records_by_id(reread.batches, batch_ids, "batch")
        _records_by_id(reread.orders, order_ids, "order")
        reservations = _reservations_with_lines(session, reservation_ids)
        if tuple(reservation.id for reservation in reservations) != reservation_ids:
            raise ProposalTargetsChangedError("Locked reservation records could not be re-read")
        target_ids = set(proposal_reservation_ids)
        targets = tuple(reservation for reservation in reservations if reservation.id in target_ids)
        if not targets or len(targets) != len(line_ids) or {reservation.allocation_line_id for reservation in targets} != set(line_ids):
            raise MissingProposalReservationsError("Proposal version must have exactly one reservation per allocation line")

        effective_at = effective_timestamp(session)
        expected_status = (AllocationReservationStatus.PENDING if source_status is AllocationProposalStatus.PENDING_APPROVAL
                           else AllocationReservationStatus.COMMITTED)
        for reservation in targets:
            line = reservation.allocation_line
            if (line is None or reservation.proposal_id != proposal_id or reservation.proposal_version_id != final_version.id
                    or line.proposal_version_id != final_version.id or reservation.reserved_quantity_kg != line.proposed_quantity_kg):
                raise InvalidReservationError("Reservation snapshot does not match its proposal line")
            if reservation.status is not expected_status or reservation.terminal_at is not None:
                raise InvalidReservationStateError("Proposal reservation is not in the expected active state")
            if expected_status is AllocationReservationStatus.COMMITTED and reservation.committed_at is None:
                raise InvalidReservationError("Committed reservation has no commitment timestamp")
            if expected_status is AllocationReservationStatus.PENDING and reservation.committed_at is not None:
                raise InvalidReservationError("Pending reservation unexpectedly has a commitment timestamp")

        terminal_status = AllocationProposalStatus.REJECTED if rejection else AllocationProposalStatus.CANCELLED
        reservation_status = AllocationReservationStatus.REJECTED if rejection else AllocationReservationStatus.CANCELLED
        for reservation in targets:
            reservation.status = reservation_status
            reservation.terminal_at = effective_at
            reservation.terminal_reason_code = code
            reservation.terminal_reason = explanation
        locked_proposal.status = terminal_status
        if rejection:
            locked_proposal.rejected_at = effective_at
        else:
            locked_proposal.cancelled_at = effective_at
        locked_proposal.terminal_reason_code = code
        locked_proposal.terminal_reason = explanation
        event = AllocationEvent(
            proposal_id=proposal_id, proposal_version_id=final_version.id,
            event_type="proposal_rejected" if rejection else "proposal_cancelled",
            details={"reservation_count": len(targets), "reason_code": code, "reason": explanation},
        )
        session.add(event)
        session.flush()
        return TerminalProposal(proposal_id, final_version.id, tuple(item.id for item in targets), event.id, effective_at, terminal_status)

    try:
        return run_reservation_transaction(session_factory, operation)
    except MissingLockedRecordsError as error:
        raise MissingReferencedRecordError("Required terminal transition target no longer exists") from error
    except DBAPIError as error:
        if is_retryable_postgres_failure(error):
            raise RetryableReservationTransactionError("Retryable PostgreSQL reservation transaction failure") from error
        raise


def reject_allocation_proposal(
    session_factory: sessionmaker[Session], proposal_id: UUID, *, reason_code: str, reason: str | None = None,
) -> TerminalProposal:
    """Reject a submitted proposal and all its PENDING reservations atomically."""
    return _terminate_allocation_proposal(session_factory, proposal_id, reason_code=reason_code, reason=reason, rejection=True)


def cancel_allocation_proposal(
    session_factory: sessionmaker[Session], proposal_id: UUID, *, reason_code: str, reason: str | None = None,
) -> TerminalProposal:
    """Cancel a pending or approved proposal and all its active reservations atomically."""
    return _terminate_allocation_proposal(session_factory, proposal_id, reason_code=reason_code, reason=reason, rejection=False)


def _expire_or_release_proposal(
    session_factory: sessionmaker[Session],
    proposal_id: UUID,
    *,
    release: bool,
    reason_code: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> TerminalProposal | None:
    if not isinstance(proposal_id, UUID):
        raise ValueError("proposal_id must be a UUID")
    code, explanation = _terminal_reason(reason_code, reason) if release else (None, None)
    if idempotency_key is not None and (
        not release or not isinstance(idempotency_key, str) or not idempotency_key.strip()
        or len(idempotency_key) > 255
    ):
        raise ValueError("Release idempotency_key must be a nonblank string of at most 255 characters")
    fingerprint = _release_fingerprint(proposal_id, code, explanation) if release and idempotency_key is not None else None

    def operation(session: Session) -> TerminalProposal | None:
        if session.get(AllocationProposal, proposal_id) is None:
            raise ProposalNotFoundError("Allocation proposal does not exist")
        discovered_version = _current_version(session, proposal_id)
        discovered_lines = _lines_for_version(session, discovered_version.id)
        discovered_fingerprint = _line_fingerprint(discovered_lines, discovered_version.id)
        line_ids = tuple(line.id for line in discovered_lines)
        batch_ids = tuple(sorted({line.batch_id for line in discovered_lines}))
        order_ids = tuple(sorted({line.order_id for line in discovered_lines}))

        lock_reservation_targets(session, batch_ids=batch_ids, order_ids=order_ids, proposal_ids=(proposal_id,))
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise ProposalTargetsChangedError("Allocation line lock target set changed")
        current_version = _current_version(session, proposal_id)
        current_lines = _lines_for_version(session, current_version.id)
        if current_version.id != discovered_version.id or _line_fingerprint(current_lines, current_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed while acquiring parent locks")
        if tuple(sorted({line.batch_id for line in locked_lines})) != batch_ids or tuple(sorted({line.order_id for line in locked_lines})) != order_ids:
            raise ProposalTargetsChangedError("Allocation line targets changed while acquiring line locks")

        reservation_ids = _reservation_ids_for_targets(session, batch_ids, order_ids)
        proposal_reservation_ids = _reservation_ids_for_proposal_version(session, proposal_id, discovered_version.id)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(session, batch_ids=batch_ids, order_ids=order_ids, proposal_ids=(proposal_id,), reservation_ids=reservation_ids)
        locked_proposal = reread.proposals[0]
        final_version = _current_version(session, proposal_id)
        final_lines = _lines_for_version(session, final_version.id)
        if final_version.id != discovered_version.id or _line_fingerprint(final_lines, final_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed after locking")
        if _reservation_ids_for_targets(session, batch_ids, order_ids) != reservation_ids:
            raise ProposalTargetsChangedError("Relevant reservation set changed while acquiring reservation locks")
        if _reservation_ids_for_proposal_version(session, proposal_id, final_version.id) != proposal_reservation_ids:
            raise ProposalTargetsChangedError("Proposal reservation set changed while acquiring reservation locks")
        _records_by_id(reread.batches, batch_ids, "batch")
        _records_by_id(reread.orders, order_ids, "order")
        reservations = _reservations_with_lines(session, reservation_ids)
        if tuple(reservation.id for reservation in reservations) != reservation_ids:
            raise ProposalTargetsChangedError("Locked reservation records could not be re-read")

        effective_at = effective_timestamp(session)
        if release and idempotency_key is not None:
            previous = session.scalar(select(AllocationOperationIdempotency).where(
                AllocationOperationIdempotency.operation_type == "release",
                AllocationOperationIdempotency.idempotency_key == idempotency_key,
            ))
            if previous is not None:
                if previous.proposal_id != proposal_id or previous.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError("Release key was used with different inputs")
                return _stored_release_result(previous)

        if not release and locked_proposal.status is not AllocationProposalStatus.PENDING_APPROVAL:
            if locked_proposal.status in {
                AllocationProposalStatus.APPROVED,
                AllocationProposalStatus.REJECTED,
                AllocationProposalStatus.CANCELLED,
                AllocationProposalStatus.EXPIRED,
                AllocationProposalStatus.RELEASED,
            }:
                return None
            raise ProposalNotTerminableError("Only a proposal awaiting approval may expire")
        if release and locked_proposal.status is not AllocationProposalStatus.APPROVED:
            raise ProposalNotTerminableError("Only an approved proposal may be released")

        target_ids = set(proposal_reservation_ids)
        targets = tuple(reservation for reservation in reservations if reservation.id in target_ids)
        if not targets or len(targets) != len(line_ids) or {reservation.allocation_line_id for reservation in targets} != set(line_ids):
            raise MissingProposalReservationsError("Proposal version must have exactly one reservation per allocation line")
        expected_status = AllocationReservationStatus.COMMITTED if release else AllocationReservationStatus.PENDING
        for reservation in targets:
            line = reservation.allocation_line
            if (line is None or reservation.proposal_id != proposal_id or reservation.proposal_version_id != final_version.id
                    or line.proposal_version_id != final_version.id or reservation.reserved_quantity_kg != line.proposed_quantity_kg):
                raise InvalidReservationError("Reservation snapshot does not match its proposal line")
            if reservation.status is not expected_status or reservation.terminal_at is not None:
                raise InvalidReservationStateError("Proposal reservation is not in the expected active state")
            if release and reservation.committed_at is None:
                raise InvalidReservationError("Committed reservation has no commitment timestamp")
            if not release and (reservation.committed_at is not None or reservation.expires_at is None
                                or reservation.expires_at != locked_proposal.expires_at):
                raise InvalidReservationError("Pending reservation expiry or commitment is inconsistent with proposal")
        if not release and (locked_proposal.expires_at is None or locked_proposal.expires_at > effective_at):
            return None

        proposal_status = AllocationProposalStatus.RELEASED if release else AllocationProposalStatus.EXPIRED
        reservation_status = AllocationReservationStatus.RELEASED if release else AllocationReservationStatus.EXPIRED
        for reservation in targets:
            reservation.status = reservation_status
            reservation.terminal_at = effective_at
            reservation.terminal_reason_code = code
            reservation.terminal_reason = explanation
        locked_proposal.status = proposal_status
        locked_proposal.terminal_reason_code = code
        locked_proposal.terminal_reason = explanation
        event = AllocationEvent(
            proposal_id=proposal_id, proposal_version_id=final_version.id,
            event_type="proposal_released" if release else "proposal_expired",
            details={"reservation_count": len(targets), "reason_code": code, "reason": explanation} if release
                    else {"reservation_count": len(targets)},
        )
        session.add(event)
        session.flush()
        result = TerminalProposal(proposal_id, final_version.id, tuple(item.id for item in targets), event.id, effective_at, proposal_status)
        if release and idempotency_key is not None:
            session.add(AllocationOperationIdempotency(
                operation_type="release", idempotency_key=idempotency_key,
                proposal_id=proposal_id, proposal_version_id=final_version.id,
                request_fingerprint=fingerprint,
                state=AllocationOperationIdempotencyState.COMPLETED,
                result_event_id=event.id,
                result_json={
                    "proposal_id": str(proposal_id),
                    "proposal_version_id": str(final_version.id),
                    "reservation_ids": [str(item.id) for item in targets],
                    "effective_at": effective_at.isoformat(),
                    "status": proposal_status.value,
                },
            ))
            session.flush()
        return result

    try:
        return run_reservation_transaction(session_factory, operation)
    except MissingLockedRecordsError as error:
        raise MissingReferencedRecordError("Required expiry or release target no longer exists") from error
    except IntegrityError as error:
        if (release and idempotency_key is not None
                and getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                == "uq_allocation_operation_idempotency_type_key"):
            raise IdempotencyConflictError("Release key was used by another request") from error
        raise
    except DBAPIError as error:
        if is_retryable_postgres_failure(error):
            raise RetryableReservationTransactionError("Retryable PostgreSQL reservation transaction failure") from error
        raise


def expire_pending_reservations(
    session_factory: sessionmaker[Session], proposal_id: UUID,
) -> TerminalProposal | None:
    """Manually expire all overdue PENDING reservations of one proposal, if due."""
    return _expire_or_release_proposal(session_factory, proposal_id, release=False)


def release_committed_reservations(
    session_factory: sessionmaker[Session], proposal_id: UUID, *, reason_code: str, reason: str | None = None,
    idempotency_key: str | None = None,
) -> TerminalProposal:
    """Release one approved proposal atomically, with optional keyed replay.

    Reason fields are stripped before fingerprinting. The key is used exactly
    as supplied, including surrounding whitespace; blank and overlong keys
    are rejected. A different key after release remains a lifecycle conflict.
    """
    result = _expire_or_release_proposal(
        session_factory, proposal_id, release=True, reason_code=reason_code,
        reason=reason, idempotency_key=idempotency_key,
    )
    assert result is not None
    return result


def validate_proposal_capacity(
    session_factory: sessionmaker[Session],
    proposal_id: UUID,
) -> ProposalCapacityValidation:
    """Validate the current proposal version against locked current capacity.

    The service selects the highest persisted ``version_number`` as current;
    there is no current-version pointer in the schema.  It follows the staged
    parent-lock, allocation-line-lock, reservation-rediscovery, reservation-lock
    protocol and returns conflicts rather than raising for insufficient capacity.
    """
    if not isinstance(proposal_id, UUID):
        raise ValueError("proposal_id must be a UUID")

    def operation(session: Session) -> ProposalCapacityValidation:
        proposal = session.get(AllocationProposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError("Allocation proposal does not exist")
        discovered_version = _current_version(session, proposal_id)
        discovered_lines = _lines_for_version(session, discovered_version.id)
        discovered_fingerprint = _line_fingerprint(discovered_lines, discovered_version.id)
        line_ids = tuple(line.id for line in discovered_lines)
        batch_ids = tuple(sorted({line.batch_id for line in discovered_lines}))
        order_ids = tuple(sorted({line.order_id for line in discovered_lines}))

        lock_reservation_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
        )
        locked_lines = lock_allocation_lines(session, line_ids)
        if tuple(line.id for line in locked_lines) != line_ids:
            raise ProposalTargetsChangedError("Allocation line lock target set changed")
        current_version = _current_version(session, proposal_id)
        current_lines = _lines_for_version(session, current_version.id)
        current_fingerprint = _line_fingerprint(current_lines, current_version.id)
        if current_version.id != discovered_version.id or current_fingerprint != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed while acquiring parent locks")

        locked_batch_ids = tuple(sorted({line.batch_id for line in locked_lines}))
        locked_order_ids = tuple(sorted({line.order_id for line in locked_lines}))
        if locked_batch_ids != batch_ids or locked_order_ids != order_ids:
            raise ProposalTargetsChangedError("Allocation line targets changed while acquiring line locks")
        reservation_ids = _reservation_ids_for_targets(session, locked_batch_ids, locked_order_ids)
        lock_reservation_targets(session, reservation_ids=reservation_ids)
        reread = reread_locked_targets(
            session,
            batch_ids=batch_ids,
            order_ids=order_ids,
            proposal_ids=(proposal_id,),
            reservation_ids=reservation_ids,
        )
        final_version = _current_version(session, proposal_id)
        final_lines = _lines_for_version(session, final_version.id)
        if final_version.id != discovered_version.id or _line_fingerprint(final_lines, final_version.id) != discovered_fingerprint:
            raise ProposalTargetsChangedError("Proposal version or allocation lines changed after locking")
        final_reservation_ids = _reservation_ids_for_targets(session, batch_ids, order_ids)
        if final_reservation_ids != reservation_ids:
            raise ProposalTargetsChangedError("Relevant reservation set changed while acquiring reservation locks")

        batches = _records_by_id(reread.batches, batch_ids, "batch")
        orders = _records_by_id(reread.orders, order_ids, "order")
        reservations = _reservations_with_lines(session, reservation_ids)
        if tuple(reservation.id for reservation in reservations) != reservation_ids:
            raise ProposalTargetsChangedError("Locked reservation records could not be re-read")
        captured_at = effective_timestamp(session)
        batch_proposed = _aggregate_lines(final_lines, "batch_id")
        order_proposed = _aggregate_lines(final_lines, "order_id")
        batch_results = tuple(
            BatchCapacity(batch_id, batch_proposed[batch_id], batch_available_kg(batches[batch_id], reservations, effective_at=captured_at))
            for batch_id in batch_ids
        )
        order_results = tuple(
            OrderCapacity(order_id, order_proposed[order_id], order_remaining_kg(orders[order_id], reservations, effective_at=captured_at))
            for order_id in order_ids
        )
        conflicts = tuple(
            CapacityConflict("batch", item.batch_id, item.proposed_quantity_kg, item.available_quantity_kg)
            for item in batch_results if item.proposed_quantity_kg > item.available_quantity_kg
        ) + tuple(
            CapacityConflict("order", item.order_id, item.proposed_quantity_kg, item.remaining_quantity_kg)
            for item in order_results if item.proposed_quantity_kg > item.remaining_quantity_kg
        )
        return ProposalCapacityValidation(
            proposal_id=proposal_id,
            proposal_version_id=final_version.id,
            capacity_valid=not conflicts,
            effective_at=captured_at,
            batches=batch_results,
            orders=order_results,
            conflicts=conflicts,
        )

    return run_reservation_transaction(session_factory, operation)
