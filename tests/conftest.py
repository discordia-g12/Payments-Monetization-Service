"""
Fixtures compartidos. Los tests de negocio corren contra PostgreSQL de verdad (ADR-001 §3: SQLite no tiene
FOR UPDATE ni índices parciales). Sin DATABASE_URL se saltean; con Docker:
`docker compose --profile test run --rm tests`.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.api.deps import get_payment_gateway, get_server_roles
from app.clients.server_roles import MembershipUnavailableError
from app.core.config import async_database_url
from app.db.session import _engine_for, get_db, get_session_factory
from app.main import app
from app.repositories import wallets
from app.services.payment_gateway import ChargeResult, PaymentGatewayError, SimulatedPaymentGateway

ROOT = Path(__file__).resolve().parents[1]

# Tablas de negocio, en un orden que respeta las claves foráneas (para TRUNCATE alcanza con CASCADE)
TABLES = (
    "point_purchases",
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


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Fábrica de sesiones sobre la base del test (para llamar a los servicios directamente)."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(sessions, gateway, server_roles) -> AsyncGenerator[AsyncClient]:
    """Cliente HTTP sobre la app, con la base, la pasarela y el Server-Roles del test (mismo event loop)."""

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    app.dependency_overrides[get_server_roles] = lambda: server_roles
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.clear()


# ── Pasarela de pago (ADR-001 §2.5) ──


class TestGateway(SimulatedPaymentGateway):
    """La pasarela simulada, con fallas a pedido para probar la saga de compra."""

    __test__ = False  # no es una clase de tests

    def __init__(self) -> None:
        super().__init__()
        self.unavailable = False  # charge() lanza: no se sabe si cobró
        self.refund_fails = False
        self.charge_calls = 0
        self.refunds: list[str] = []

    async def charge(self, **kwargs) -> ChargeResult:
        self.charge_calls += 1
        if self.unavailable:
            raise PaymentGatewayError("la pasarela no responde")
        return await super().charge(**kwargs)

    async def refund(self, reference: str) -> None:
        if self.refund_fails:
            raise PaymentGatewayError("no se pudo devolver")
        self.refunds.append(reference)
        await super().refund(reference)


@pytest.fixture
def gateway() -> TestGateway:
    return TestGateway()


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
    """Server-Roles de mentira: quién es miembro de qué servidor, y si responde (lo usa el fixture `client`)."""

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
            raise MembershipUnavailableError("Server-Roles no responde")
        await asyncio.sleep(0)
        return self.members.get(server_id)


@pytest.fixture
def server_roles() -> FakeServerRoles:
    return FakeServerRoles()


# ── Datos ──


@pytest.fixture
def give_points(sessions) -> Callable[[str, int], Awaitable[None]]:
    """`await give_points("u1", 1000)`: le carga saldo a un usuario (un movimiento `adjustment`), sin comprar."""

    async def give(user_id: str, amount: int) -> None:
        async with sessions() as session:
            wallet = await wallets.lock_wallet(session, user_id)
            wallets.apply_movement(session, wallet, amount, "adjustment")
            await session.commit()

    return give
