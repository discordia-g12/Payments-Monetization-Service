import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from sqlalchemy import text

from app.api.deps import get_payment_gateway
from app.api.v1 import boosts, points
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.db.session import DatabaseNotConfiguredError, get_engine, get_sessionmaker
from app.services.points import reconcile_pending_purchases

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("payments")

READYZ_TIMEOUT_SECONDS = 3
RECONCILE_INTERVAL_SECONDS = 60


async def reconcile_forever() -> None:
    """
    Conciliación de compras a medias (ADR-001 §2.11), cada minuto. Corre en el proceso de la API y no en un
    worker aparte: la pasarela simulada guarda sus cobros en memoria y solo los ve este proceso.
    """
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            closed = await reconcile_pending_purchases(get_sessionmaker(), get_payment_gateway())
            if closed:
                logger.info("Conciliación: %s compras cerradas", closed)
        except Exception:
            logger.exception("Falló la vuelta de conciliación; se reintenta en %ss", RECONCILE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(reconcile_forever()) if get_settings().database_url else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Payments & Monetization Service", lifespan=lifespan)
register_error_handlers(app)
app.include_router(points.router)
app.include_router(boosts.router)


@app.get("/")
def read_root():
    return {"message": "Welcome to Payments & Monetization Service"}


@app.get("/livez", tags=["Health"])
def livez():
    return {"status": "alive"}


@app.get("/readyz", tags=["Health"])
async def readyz(response: Response):
    try:
        engine = get_engine()
    except DatabaseNotConfiguredError as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "error": str(exc)}
    try:
        async with asyncio.timeout(READYZ_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        # El detalle va al log, no a la respuesta (puede tener el host de la base)
        logger.warning("readyz: la base no responde: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "error": "database unreachable"}
