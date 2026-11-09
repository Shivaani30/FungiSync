from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError

from app.api.routes.health import router as health_router
from app.api.routes.growers import router as growers_router
from app.api.routes.batches import router as batches_router
from app.api.routes.buyers import router as buyers_router
from app.api.routes.orders import router as orders_router
from app.api.routes.matches import router as matches_router
from app.core.config import get_settings


settings = get_settings()
app = FastAPI(title="FungiSync Distribution Intelligence", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(growers_router)
app.include_router(batches_router)
app.include_router(buyers_router)
app.include_router(orders_router)
app.include_router(matches_router)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_, __):
    return JSONResponse(status_code=409, content={"detail": "Database integrity constraint violated"})
