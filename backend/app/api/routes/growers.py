from app.api.routes.crud import router_for
from app.models import Grower
from app.schemas import GrowerCreate, GrowerOut, GrowerUpdate
from app.services import CRUDService
router = router_for("/growers", "growers", CRUDService(Grower), GrowerCreate, GrowerUpdate, GrowerOut, ("location",))
