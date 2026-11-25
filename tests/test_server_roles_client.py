"""Cliente HTTP de Server-Roles (ADR-001 §2.9). Tests unitarios con httpx.MockTransport, sin red."""

import httpx
import pytest

from app.clients.server_roles import HttpServerRolesClient, MembershipUnavailableError, UnconfiguredServerRolesClient

SERVER = "11111111-1111-1111-1111-111111111111"


def client_with(handler) -> HttpServerRolesClient:
    return HttpServerRolesClient("http://server-roles:8080/", 2.0, transport=httpx.MockTransport(handler))


async def test_devuelve_los_miembros():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"server_id": SERVER, "user_ids": ["u1", "u2"]})

    assert await client_with(handler).member_ids(SERVER) == {"u1", "u2"}
    assert seen == [f"/internal/servers/{SERVER}/member-ids"]


@pytest.mark.parametrize("status_code", [404, 422])
async def test_un_servidor_que_no_existe_es_none(status_code):
    client = client_with(lambda request: httpx.Response(status_code, json={"detail": "no"}))
    assert await client.member_ids(SERVER) is None


@pytest.mark.parametrize("status_code", [500, 503, 401])
async def test_otra_respuesta_es_que_no_esta_disponible(status_code):
    client = client_with(lambda request: httpx.Response(status_code))
    with pytest.raises(MembershipUnavailableError):
        await client.member_ids(SERVER)


async def test_sin_conexion_no_esta_disponible():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(MembershipUnavailableError):
        await client_with(handler).member_ids(SERVER)


async def test_una_respuesta_rara_no_esta_disponible():
    client = client_with(lambda request: httpx.Response(200, json={"otra": "cosa"}))
    with pytest.raises(MembershipUnavailableError):
        await client.member_ids(SERVER)


async def test_sin_configurar_nunca_deja_pasar():
    with pytest.raises(MembershipUnavailableError):
        await UnconfiguredServerRolesClient().member_ids(SERVER)
