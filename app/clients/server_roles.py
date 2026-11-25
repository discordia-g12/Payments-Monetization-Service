"""Cliente de Server-Roles: quiénes son miembros de un servidor (ADR-001 §2.9)."""

import logging
from typing import Protocol

import httpx

logger = logging.getLogger("payments.server_roles")


class MembershipUnavailableError(Exception):
    """Server-Roles no respondió o respondió mal: no se puede verificar la membresía."""


class ServerRolesClient(Protocol):
    async def member_ids(self, server_id: str) -> set[str] | None:
        """Los ids de los miembros, o None si el servidor no existe. Lanza MembershipUnavailableError."""
        ...


class HttpServerRolesClient:
    """
    `GET /internal/servers/{id}/member-ids`, como Chat y Notification. Es una ruta interna (no pasa por Kong):
    Payments le llega directo, por la VPC, con la regla infra → edge:8002 del Security Group.
    """

    def __init__(
        self, base_url: str, timeout_seconds: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        # transport: solo para los tests (httpx.MockTransport)
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport)

    async def member_ids(self, server_id: str) -> set[str] | None:
        try:
            response = await self._http.get(f"/internal/servers/{server_id}/member-ids")
        except httpx.HTTPError as exc:
            logger.warning("Server-Roles no respondió (member-ids de %s): %s", server_id, exc)
            raise MembershipUnavailableError from exc
        # 404: el servidor no existe. 422: el id no es un UUID (tampoco existe)
        if response.status_code in (404, 422):
            return None
        if response.status_code != 200:
            logger.warning("Server-Roles respondió %s (member-ids de %s)", response.status_code, server_id)
            raise MembershipUnavailableError
        try:
            return set(response.json()["user_ids"])
        except (ValueError, KeyError, TypeError) as exc:
            raise MembershipUnavailableError from exc

    async def aclose(self) -> None:
        await self._http.aclose()


class UnconfiguredServerRolesClient:
    """Sin SERVER_ROLES_URL: no se puede verificar a nadie (boostear responde 503, nunca deja pasar)."""

    async def member_ids(self, server_id: str) -> set[str] | None:
        raise MembershipUnavailableError("SERVER_ROLES_URL no está configurada")
