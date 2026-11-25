"""
Historia: Usar puntos para boostear un servidor (ADR-001 §2.3, §2.7, §2.9). Contra PostgreSQL.

Un boost cuesta 500 puntos. Niveles: 1 con 5 boosts, 2 con 15, 3 con 30.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models import OutboxEvent, PointTransaction, ServerBoost, ServerBoostCounter, Wallet
from app.services.boost_levels import BOOST_COST_POINTS, level_for

SERVER = "11111111-1111-1111-1111-111111111111"
OTHER_SERVER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def members(server_roles):
    """u1, u2 y u3 son miembros de SERVER."""
    server_roles.add_member(SERVER, "u1", "u2", "u3")
    return server_roles


async def boost(client, as_user, user="u1", key=None, server=SERVER):
    return await client.post(
        f"/api/v1/boosts/servers/{server}", headers=as_user(user, idempotency_key=key or str(uuid.uuid4()))
    )


async def seed_boosts(sessions, count: int, server=SERVER) -> None:
    """El servidor ya tiene `count` boosts vigentes (de otros miembros): solo el contador, para no boostear N veces."""
    async with sessions() as session:
        session.add(ServerBoostCounter(server_id=server, active_boosts=count, level=level_for(count)))
        await session.commit()


async def events(db_session, event_type: str) -> list[OutboxEvent]:
    result = await db_session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    return list(result.scalars())


async def counter(db_session, server=SERVER) -> ServerBoostCounter:
    db_session.expire_all()
    return await db_session.get(ServerBoostCounter, server)


async def balance(db_session, user="u1") -> int:
    db_session.expire_all()
    wallet = await db_session.get(Wallet, user)
    return wallet.balance if wallet else 0


async def count(db_session, model) -> int:
    return await db_session.scalar(select(func.count()).select_from(model))


def test_el_nivel_sale_de_los_umbrales():
    assert [level_for(n) for n in (0, 4, 5, 14, 15, 29, 30, 100)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert BOOST_COST_POINTS == 500


# ── CA 1: Boost exitoso ──


async def test_ca1_boost_descuenta_los_puntos_y_suma_al_servidor(client, as_user, db_session, members, give_points):
    await give_points("u1", 1000)

    response = await boost(client, as_user)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["balance"] == 500
    assert body["server"] == {"server_id": SERVER, "active_boosts": 1, "level": 0}
    assert body["boost"]["points_spent"] == 500
    assert body["boost"]["removed_at"] is None

    saved = (await db_session.execute(select(ServerBoost))).scalar_one()
    assert (saved.server_id, saved.user_id, saved.points_spent) == (SERVER, "u1", 500)
    payment = (await db_session.execute(select(PointTransaction).where(PointTransaction.kind == "boost"))).scalar_one()
    assert (payment.amount, payment.balance_after, payment.server_boost_id) == (-500, 500, saved.id)


async def test_ca1_un_miembro_puede_boostear_varias_veces(client, as_user, db_session, members, give_points):
    await give_points("u1", 1500)

    responses = [await boost(client, as_user) for _ in range(3)]

    assert [r.status_code for r in responses] == [201, 201, 201]
    assert len({r.json()["boost"]["id"] for r in responses}) == 3
    assert (await counter(db_session)).active_boosts == 3
    assert await balance(db_session) == 0
    mine = (await client.get("/api/v1/boosts/mine", params={"server_id": SERVER}, headers=as_user("u1"))).json()
    assert len(mine["boosts"]) == 3


async def test_ca1_el_boost_deja_server_boosted_en_el_outbox(client, as_user, db_session, members, give_points):
    await give_points("u1", 500)
    response = await boost(client, as_user)

    [event] = await events(db_session, "server.boosted")
    assert event.aggregate_id == SERVER
    assert event.payload["boost_id"] == response.json()["boost"]["id"]
    assert (event.payload["user_id"], event.payload["points_spent"], event.payload["active_boosts"]) == ("u1", 500, 1)


# ── CA 2: Saldo insuficiente ──


async def test_ca2_sin_saldo_suficiente_no_boostea_e_informa_lo_que_falta(
    client, as_user, db_session, members, give_points
):
    await give_points("u1", 300)

    response = await boost(client, as_user)

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "insufficient_points"
    assert (problem["balance"], problem["cost"], problem["missing"]) == (300, 500, 200)
    assert "200" in problem["detail"]
    assert await balance(db_session) == 300
    assert await count(db_session, ServerBoost) == 0
    assert await counter(db_session) is None


async def test_ca2_sin_billetera_falta_el_costo_entero(client, as_user, members):
    response = await boost(client, as_user)
    assert response.status_code == 409
    assert response.json()["missing"] == 500


async def test_ca2_un_boost_fallido_no_gasta_la_clave(client, as_user, members, give_points):
    """Si no alcanzó, el reintento con la misma clave (después de cargar saldo) se ejecuta de verdad."""
    first = await boost(client, as_user, key="k1")
    await give_points("u1", 500)
    again = await boost(client, as_user, key="k1")

    assert (first.status_code, again.status_code) == (409, 201)


# ── CA 3: El servidor alcanza un nuevo nivel ──


@pytest.mark.parametrize("before, level", [(3, 0), (4, 1), (13, 1), (14, 2), (28, 2), (29, 3), (40, 3)])
async def test_ca3_el_nivel_sale_de_los_boosts_vigentes(client, as_user, sessions, members, give_points, before, level):
    await seed_boosts(sessions, before)
    await give_points("u1", 500)

    response = await boost(client, as_user)

    assert response.json()["server"] == {"server_id": SERVER, "active_boosts": before + 1, "level": level}


@pytest.mark.parametrize("before, previous, level", [(4, 0, 1), (14, 1, 2), (29, 2, 3)])
async def test_ca3_el_boost_que_cruza_el_umbral_sube_el_nivel_y_lo_avisa(
    client, as_user, db_session, sessions, members, give_points, before, previous, level
):
    await seed_boosts(sessions, before)
    await give_points("u1", 500)

    response = await boost(client, as_user)

    assert response.json()["server"]["level"] == level
    [event] = await events(db_session, "server.boost_level_changed")
    assert event.aggregate_id == SERVER
    assert (event.payload["previous_level"], event.payload["level"]) == (previous, level)
    assert event.payload["active_boosts"] == before + 1


async def test_ca3_boosts_que_no_cruzan_umbral_no_avisan_cambio_de_nivel(
    client, as_user, db_session, sessions, members, give_points
):
    await seed_boosts(sessions, 5)
    await give_points("u1", 2000)

    for _ in range(4):
        assert (await boost(client, as_user)).status_code == 201

    assert await events(db_session, "server.boost_level_changed") == []
    assert (await counter(db_session)).level == 1


async def test_ca3_boosts_simultaneos_cruzan_el_umbral_una_sola_vez(
    client, as_user, db_session, sessions, members, give_points
):
    await seed_boosts(sessions, 4)
    for user in ("u1", "u2", "u3"):
        await give_points(user, 500)

    responses = await asyncio.gather(*(boost(client, as_user, user=user) for user in ("u1", "u2", "u3")))

    assert [r.status_code for r in responses] == [201, 201, 201]
    saved = await counter(db_session)
    assert (saved.active_boosts, saved.level) == (7, 1)
    assert len(await events(db_session, "server.boost_level_changed")) == 1


# ── CA 4: Retirar boost propio ──


async def test_ca4_retirar_resta_del_servidor_sin_reembolso(client, as_user, db_session, members, give_points):
    await give_points("u1", 500)
    boost_id = (await boost(client, as_user)).json()["boost"]["id"]

    response = await client.delete(f"/api/v1/boosts/{boost_id}", headers=as_user("u1"))

    assert response.status_code == 200, response.text
    assert response.json()["server"] == {"server_id": SERVER, "active_boosts": 0, "level": 0}
    assert response.json()["boost"]["removed_at"] is not None
    assert await balance(db_session) == 0  # sin reembolso
    [event] = await events(db_session, "server.boost_removed")
    assert event.payload["boost_id"] == boost_id
    mine = (await client.get("/api/v1/boosts/mine", headers=as_user("u1"))).json()
    assert mine["boosts"] == []


async def test_ca4_retirar_por_debajo_del_umbral_baja_el_nivel(
    client, as_user, db_session, sessions, members, give_points
):
    await seed_boosts(sessions, 4)
    await give_points("u1", 500)
    boost_id = (await boost(client, as_user)).json()["boost"]["id"]  # 5: nivel 1

    response = await client.delete(f"/api/v1/boosts/{boost_id}", headers=as_user("u1"))

    assert response.json()["server"] == {"server_id": SERVER, "active_boosts": 4, "level": 0}
    changes = await events(db_session, "server.boost_level_changed")
    assert sorted((e.payload["previous_level"], e.payload["level"]) for e in changes) == [(0, 1), (1, 0)]


async def test_ca4_no_se_puede_retirar_el_boost_de_otro(client, as_user, db_session, members, give_points):
    await give_points("u1", 500)
    boost_id = (await boost(client, as_user, user="u1")).json()["boost"]["id"]

    response = await client.delete(f"/api/v1/boosts/{boost_id}", headers=as_user("u2"))

    assert response.status_code == 404
    assert response.json()["code"] == "boost_not_found"
    assert (await counter(db_session)).active_boosts == 1


async def test_ca4_retirar_dos_veces_el_mismo_boost(client, as_user, db_session, sessions, members, give_points):
    await seed_boosts(sessions, 2)
    await give_points("u1", 500)
    boost_id = (await boost(client, as_user)).json()["boost"]["id"]

    first = await client.delete(f"/api/v1/boosts/{boost_id}", headers=as_user("u1"))
    second = await client.delete(f"/api/v1/boosts/{boost_id}", headers=as_user("u1"))

    assert (first.status_code, second.status_code) == (200, 409)
    assert second.json()["code"] == "boost_already_removed"
    assert (await counter(db_session)).active_boosts == 2


async def test_retirar_un_boost_que_no_existe(client, as_user):
    response = await client.delete(f"/api/v1/boosts/{uuid.uuid4()}", headers=as_user("u1"))
    assert response.status_code == 404


# ── Membresía (ADR-001 §2.9) ──


async def test_un_no_miembro_no_puede_boostear(client, as_user, db_session, members, give_points):
    await give_points("intruso", 500)

    response = await boost(client, as_user, user="intruso")

    assert response.status_code == 403
    assert response.json()["code"] == "not_a_member"
    assert await balance(db_session, "intruso") == 500
    assert await count(db_session, ServerBoost) == 0


async def test_un_servidor_que_no_existe_responde_404(client, as_user, members, give_points):
    await give_points("u1", 500)
    response = await boost(client, as_user, server=OTHER_SERVER)
    assert response.status_code == 404
    assert response.json()["code"] == "server_not_found"


async def test_si_server_roles_no_responde_no_se_boostea(client, as_user, db_session, members, give_points):
    await give_points("u1", 500)
    members.available = False

    response = await boost(client, as_user)

    assert response.status_code == 503
    assert response.json()["code"] == "membership_unavailable"
    assert await balance(db_session) == 500
    assert await count(db_session, ServerBoost) == 0


async def test_un_id_de_servidor_invalido_responde_422(client, as_user):
    response = await client.post("/api/v1/boosts/servers/no-es-un-uuid", headers=as_user("u1", idempotency_key="k"))
    assert response.status_code == 422


# ── Idempotencia y concurrencia ──


async def test_reintentar_el_boost_con_la_misma_clave_no_cobra_dos_veces(
    client, as_user, db_session, members, give_points
):
    await give_points("u1", 1000)

    first = await boost(client, as_user, key="mismo-intento")
    again = await boost(client, as_user, key="mismo-intento")

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert await count(db_session, ServerBoost) == 1
    assert await balance(db_session) == 500


async def test_la_misma_clave_para_otro_servidor_responde_422(client, as_user, members, give_points):
    members.add_member(OTHER_SERVER, "u1")
    await give_points("u1", 1000)

    await boost(client, as_user, key="k1", server=SERVER)
    response = await boost(client, as_user, key="k1", server=OTHER_SERVER)

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


async def test_boostear_sin_idempotency_key_responde_400(client, as_user, members, give_points):
    await give_points("u1", 500)
    response = await client.post(f"/api/v1/boosts/servers/{SERVER}", headers=as_user("u1"))
    assert response.status_code == 400


async def test_dos_pedidos_simultaneos_con_la_misma_clave_boostean_una_vez(
    client, as_user, db_session, members, give_points
):
    await give_points("u1", 1000)

    responses = await asyncio.gather(*(boost(client, as_user, key="la-misma") for _ in range(4)))

    assert {r.status_code for r in responses} == {201}
    assert await count(db_session, ServerBoost) == 1
    assert await balance(db_session) == 500


async def test_boosts_simultaneos_del_mismo_usuario_no_dejan_saldo_negativo(
    client, as_user, db_session, members, give_points
):
    await give_points("u1", 1000)

    responses = await asyncio.gather(*(boost(client, as_user) for _ in range(3)))

    assert sorted(r.status_code for r in responses) == [201, 201, 409]
    assert await balance(db_session) == 0
    assert (await counter(db_session)).active_boosts == 2
