from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import async_database_url, get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """Falta DATABASE_URL."""


@lru_cache
def _engine_for(url: str) -> AsyncEngine:
    return create_async_engine(
        async_database_url(url),
        pool_pre_ping=True,
        # Un nodo chico (192 MB): pocas conexiones, y el RDS es compartido con los demás servicios
        pool_size=5,
        max_overflow=5,
    )


def get_engine() -> AsyncEngine:
    """El engine se crea recién al usarlo: sin DATABASE_URL la app arranca igual (/readyz informa 503)."""
    url = get_settings().database_url
    if not url:
        raise DatabaseNotConfiguredError("DATABASE_URL not set")
    return _engine_for(url)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Dependencia de FastAPI para lo que necesita varias transacciones en un mismo pedido (la saga de compra,
    ADR-001 §2.11): cada paso abre la suya.
    """
    return get_sessionmaker()


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por pedido. Cada servicio abre y cierra sus transacciones."""
    async with get_sessionmaker()() as session:
        yield session
