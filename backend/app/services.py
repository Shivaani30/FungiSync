from typing import Any
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

class CRUDService:
    def __init__(self, model: type): self.model = model
    def get(self, db: Session, item_id: UUID):
        item = db.get(self.model, item_id)
        if item is None: raise HTTPException(status_code=404, detail=f"{self.model.__name__} not found")
        return item
    def list(self, db: Session, **filters: Any):
        stmt = select(self.model)
        for field, value in filters.items():
            if value is not None: stmt = stmt.where(getattr(self.model, field) == value)
        return list(db.scalars(stmt.order_by(self.model.created_at.desc())))
    def create(self, db: Session, values: dict):
        item = self.model(**values); db.add(item); return self._save(db, item)
    def update(self, db: Session, item_id: UUID, values: dict):
        item = self.get(db, item_id)
        for field, value in values.items(): setattr(item, field, value)
        return self._save(db, item)
    def delete(self, db: Session, item_id: UUID):
        item = self.get(db, item_id); db.delete(item)
        try: db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Record has dependent records")
    def _save(self, db: Session, item):
        try: db.commit()
        except IntegrityError: db.rollback(); raise HTTPException(status_code=409, detail="Database integrity constraint violated")
        db.refresh(item); return item
