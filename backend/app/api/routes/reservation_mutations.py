"""HTTP adapters for reservation-aware batch and order PATCH operations."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.reservation_mutation_service import (
    MutationTargetNotFoundError,
    MutationTargetsChangedError,
    ReservationMutationConflictError,
    update_batch_reservation_aware,
    update_order_reservation_aware,
)
from app.reservation_transaction import MissingLockedRecordsError


def _update(db: Session, item_id: UUID, values: dict, *, batch: bool) -> object:
    # Bind the fresh transaction to the request's engine, including the
    # PostgreSQL test-engine override.  Never use the global development bind.
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    operation = update_batch_reservation_aware if batch else update_order_reservation_aware
    try:
        return operation(factory, item_id, values)
    except MutationTargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (MutationTargetsChangedError, MissingLockedRecordsError):
        raise HTTPException(status_code=409, detail="Allocation targets changed; retry the update") from None
    except ReservationMutationConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Database integrity constraint violated") from error


def update_batch(db: Session, batch_id: UUID, values: dict) -> object:
    return _update(db, batch_id, values, batch=True)


def update_order(db: Session, order_id: UUID, values: dict) -> object:
    return _update(db, order_id, values, batch=False)
