import asyncio
import logging

from fastapi import FastAPI, Response, status
from sqlalchemy import text

from app.db.session import DatabaseNotConfiguredError, get_engine

logger = logging.getLogger("payments")

app = FastAPI(title="Payments & Monetization Service")

READYZ_TIMEOUT_SECONDS = 3


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
