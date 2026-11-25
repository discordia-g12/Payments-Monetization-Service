from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.server_roles import HttpServerRolesClient, ServerRolesClient, UnconfiguredServerRolesClient
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.services.payment_gateway import PaymentGateway, SimulatedPaymentGateway

IDEMPOTENCY_KEY_MAX_LENGTH = 255


async def get_current_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """El usuario lo pone Kong (X-User-Id) después de validar el token. Sin él, el pedido no pasó por Kong."""
    if not x_user_id or not x_user_id.strip():
        raise AppError(status.HTTP_401_UNAUTHORIZED, "unauthenticated", "Falta la identidad del usuario.")
    return x_user_id.strip()


async def get_idempotency_key(idempotency_key: Annotated[str | None, Header()] = None) -> str:
    """Obligatorio en lo que cobra o gasta puntos (ADR-001 §2.4): lo genera el cliente para cada intento."""
    key = (idempotency_key or "").strip()
    if not key:
        raise AppError(
            status.HTTP_400_BAD_REQUEST,
            "idempotency_key_required",
            "Falta el header Idempotency-Key: generá uno por intento (por ejemplo, un UUID).",
        )
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise AppError(
            status.HTTP_400_BAD_REQUEST,
            "idempotency_key_invalid",
            f"Idempotency-Key puede tener hasta {IDEMPOTENCY_KEY_MAX_LENGTH} caracteres.",
        )
    return key


# Una sola pasarela por proceso: la simulada guarda sus cobros en memoria (los ve la conciliación)
_gateway = SimulatedPaymentGateway()


def get_payment_gateway() -> PaymentGateway:
    return _gateway


@lru_cache
def _server_roles_for(url: str | None, timeout_seconds: float) -> ServerRolesClient:
    if not url:
        return UnconfiguredServerRolesClient()
    return HttpServerRolesClient(url, timeout_seconds)


def get_server_roles() -> ServerRolesClient:
    """Un cliente por proceso (reutiliza las conexiones). Sin SERVER_ROLES_URL, boostear responde 503."""
    settings = get_settings()
    return _server_roles_for(settings.server_roles_url, settings.server_roles_timeout_seconds)


CurrentUser = Annotated[str, Depends(get_current_user_id)]
IdempotencyKey = Annotated[str, Depends(get_idempotency_key)]
Gateway = Annotated[PaymentGateway, Depends(get_payment_gateway)]
ServerRoles = Annotated[ServerRolesClient, Depends(get_server_roles)]
Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
