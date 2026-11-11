from app.api.routes.crud import router_for
from app.models import Batch, Grower
from app.schemas import BatchCreate, BatchOut, BatchUpdate
from app.services import CRUDService
from app.api.routes.reservation_mutations import update_batch
router = router_for("/batches", "batches", CRUDService(Batch), BatchCreate, BatchUpdate, BatchOut, ("grower_id", "status", "species"), CRUDService(Grower), "grower_id", update_batch)
