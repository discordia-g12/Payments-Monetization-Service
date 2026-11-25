"""
Fixtures compartidos. Los tests de negocio corren contra PostgreSQL de verdad (ADR-001 §3: SQLite no tiene
FOR UPDATE ni índices parciales). Sin DATABASE_URL se saltean; con Docker:
`docker compose --profile test run --rm tests`.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import async_database_url
from app.db.session import _engine_for, get_db
from app.main import app

ROOT = Path(__file__).resolve().parents[1]

# Tablas de negocio, en un orden que respeta las claves foráneas (para TRUNCATE alcanza con CASCADE)
TABLES = (
    "point_transactions",
    "server_boosts",
    "server_boost_counters",
    "wallets",
    "subscriptions",
    "idempotency_records",
    "outbox_events",
)


@pytest.fixture(autouse=True)
def fresh_engine():
    """
    Un engine nuevo por test: las conexiones de asyncpg quedan atadas al event loop que las abrió, y cada test
    (y cada pedido del TestClient) corre en uno distinto.
    """
    _engine_for.cache_clear()
    yield
    _engine_for.cache_clear()


# ── Base de datos ──


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("sin DATABASE_URL (en el CI hay un Postgres; en local: docker compose --profile test)")
    return url


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    """La base en la última migración, una vez por corrida (sincrónico: env.py usa asyncio.run)."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def db_engine(migrated: str) -> AsyncGenerator[AsyncEngine]:
    """Engine del test, con la base vacía al empezar (cada test parte de cero)."""
    engine = create_async_engine(async_database_url(migrated))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Una sesión para preparar datos o revisar lo que quedó en la base."""
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        yield session


# ── API ──


@pytest_asyncio.fixture
async def client(db_engine: AsyncEngine) -> AsyncGenerator[AsyncClient]:
    """Cliente HTTP sobre la app, con la base del test (mismo event loop que el test)."""
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def as_user() -> Callable[..., dict[str, str]]:
    """Headers de un pedido autenticado, como los pone Kong. `as_user("u1", idempotency_key="k1")`."""

    def headers(user_id: str, idempotency_key: str | None = None) -> dict[str, str]:
        result = {"X-User-Id": user_id}
        if idempotency_key is not None:
            result["Idempotency-Key"] = idempotency_key
        return result

    return headers


# ── Server-Roles (membresía, ADR-001 §2.9) ──


class FakeServerRoles:
    """
    Server-Roles de mentira: quién es miembro de qué servidor, y si responde. Se conecta a la app cuando exista
    el cliente de membresía (historia de boosts), con app.dependency_overrides.
    """

    def __init__(self) -> None:
        self.members: dict[str, set[str]] = {}
        self.available = True
        self.calls = 0

    def add_member(self, server_id: str, *user_ids: str) -> None:
        self.members.setdefault(server_id, set()).update(user_ids)

    async def member_ids(self, server_id: str) -> set[str] | None:
        """None: el servidor no existe. Lanza si Server-Roles "está caído"."""
        self.calls += 1
        if not self.available:
            raise ConnectionError("Server-Roles no responde")
        await asyncio.sleep(0)
        return self.members.get(server_id)


@pytest.fixture
def server_roles() -> FakeServerRoles:
    return FakeServerRoles()
