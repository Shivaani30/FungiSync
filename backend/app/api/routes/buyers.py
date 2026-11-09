from app.api.routes.crud import router_for
from app.models import Buyer
from app.schemas import BuyerCreate, BuyerOut, BuyerUpdate
from app.services import CRUDService
router = router_for("/buyers", "buyers", CRUDService(Buyer), BuyerCreate, BuyerUpdate, BuyerOut, ("location",))
