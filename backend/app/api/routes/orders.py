from app.api.routes.crud import router_for
from app.models import Buyer, Order
from app.schemas import OrderCreate, OrderOut, OrderUpdate
from app.services import CRUDService
router = router_for("/orders", "orders", CRUDService(Order), OrderCreate, OrderUpdate, OrderOut, ("buyer_id", "status", "species"), CRUDService(Buyer), "buyer_id")
