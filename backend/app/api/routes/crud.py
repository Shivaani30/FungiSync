from typing import Any, Callable
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services import CRUDService

def router_for(prefix: str, tag: str, service: CRUDService, create_schema, update_schema, out_schema, filters: tuple[str, ...] = (), parent_service: CRUDService | None = None, parent_field: str | None = None, update_handler: Callable[[Session, UUID, dict], object] | None = None) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    @router.post("", response_model=out_schema, status_code=201)
    def create(payload: create_schema, db: Session = Depends(get_db)):
        values = payload.model_dump()
        if parent_service and parent_field: parent_service.get(db, values[parent_field])
        return service.create(db, values)
    @router.get("", response_model=list[out_schema])
    def list_items(db: Session = Depends(get_db), grower_id: UUID | None = Query(None), buyer_id: UUID | None = Query(None), status: str | None = Query(None), species: str | None = Query(None), location: str | None = Query(None)):
        requested = {"grower_id": grower_id, "buyer_id": buyer_id, "status": status, "species": species, "location": location}
        return service.list(db, **{key: requested[key] for key in filters})
    @router.get("/{item_id}", response_model=out_schema)
    def get_item(item_id: UUID, db: Session = Depends(get_db)): return service.get(db, item_id)
    @router.patch("/{item_id}", response_model=out_schema)
    def update(item_id: UUID, payload: update_schema, db: Session = Depends(get_db)):
        values = payload.model_dump(exclude_unset=True)
        if parent_service and parent_field and parent_field in values: parent_service.get(db, values[parent_field])
        if update_handler is not None:
            return update_handler(db, item_id, values)
        return service.update(db, item_id, values)
    @router.delete("/{item_id}", status_code=204)
    def delete(item_id: UUID, db: Session = Depends(get_db)):
        service.delete(db, item_id)
    return router
