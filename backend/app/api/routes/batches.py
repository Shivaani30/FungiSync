from app.api.routes.crud import router_for
from app.models import Batch, Grower
from app.schemas import BatchCreate, BatchOut, BatchUpdate
from app.services import CRUDService
router = router_for("/batches", "batches", CRUDService(Batch), BatchCreate, BatchUpdate, BatchOut, ("grower_id", "status", "species"), CRUDService(Grower), "grower_id")
