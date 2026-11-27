"""Historia: Ver beneficios de servidor boosteado (ADR-001 §2.7, §2.10). Contra PostgreSQL."""

import uuid

import pytest

from app.models import ServerBoostCounter
from app.services.boost_levels import level_for, next_level

SERVER = "11111111-1111-1111-1111-111111111111"
OTHER_SERVER = "22222222-2222-2222-2222-222222222222"
INFO = f"/api/v1/boosts/servers/{SERVER}"


@pytest.fixture
def members(server_roles):
    server_roles.add_member(SERVER, "u1", "u2")
    return server_roles


async def seed_boosts(sessions, count: int, server=SERVER) -> None:
    """El servidor ya tiene `count` boosts vigentes: solo el contador, para no boostear N veces."""
    async with sessions() as session:
        session.add(ServerBoostCounter(server_id=server, active_boosts=count, level=level_for(count)))
        await session.commit()


async def info(client, as_user, user="u1", server=SERVER):
    return await client.get(f"/api/v1/boosts/servers/{server}", headers=as_user(user))


def benefit_ids(benefits) -> list[str]:
    return [b["id"] for b in benefits]


def test_proximo_nivel_y_cuanto_falta():
    assert [next_level(n) for n in (0, 3, 5, 14, 15, 29, 30, 50)] == [
        (1, 5),
        (1, 2),
        (2, 10),
        (2, 1),
        (3, 15),
        (3, 1),
        (None, 0),
        (None, 0),
    ]


# ── CA 1: Ver nivel y beneficios actuales ──


async def test_ca1_nivel_y_beneficios_desbloqueados(client, as_user, sessions, members):
    await seed_boosts(sessions, 15)

    response = await info(client, as_user)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["level"], body["active_boosts"]) == (2, 15)
    assert benefit_ids(body["unlocked_benefits"]) == [
        "emoji_slots_20",
        "animated_icon",
        "server_banner",
        "attachments_25mb",
    ]
    assert [b["level"] for b in body["unlocked_benefits"]] == [1, 1, 2, 2]
    assert all(b["name"] and b["description"] for b in body["unlocked_benefits"])


async def test_ca1_un_servidor_sin_boosts_esta_en_nivel_0(client, as_user, members):
    response = await info(client, as_user)

    assert response.status_code == 200
    body = response.json()
    assert (body["level"], body["active_boosts"], body["unlocked_benefits"]) == (0, 0, [])


async def test_ca1_muestra_todos_los_niveles_con_lo_que_desbloquea_cada_uno(client, as_user, sessions, members):
    await seed_boosts(sessions, 5)

    levels = (await info(client, as_user)).json()["levels"]

    assert [(lvl["level"], lvl["required_boosts"], lvl["unlocked"]) for lvl in levels] == [
        (1, 5, True),
        (2, 15, False),
        (3, 30, False),
    ]
    assert benefit_ids(levels[2]["benefits"]) == ["vanity_url", "boosted_badge"]


async def test_ca1_en_el_nivel_3_estan_todos_los_beneficios(client, as_user, sessions, members):
    await seed_boosts(sessions, 30)
    body = (await info(client, as_user)).json()

    assert body["level"] == 3
    assert len(body["unlocked_benefits"]) == 6
    assert all(lvl["unlocked"] for lvl in body["levels"])


# ── CA 2: Ver progreso al siguiente nivel ──


@pytest.mark.parametrize(
    "boosts, next_lvl, missing",
    [(0, 1, 5), (3, 1, 2), (5, 2, 10), (14, 2, 1), (15, 3, 15), (29, 3, 1)],
)
async def test_ca2_cuantos_boosts_faltan_para_el_siguiente_nivel(
    client, as_user, sessions, members, boosts, next_lvl, missing
):
    if boosts:
        await seed_boosts(sessions, boosts)

    body = (await info(client, as_user)).json()

    assert body["active_boosts"] == boosts
    assert (body["next_level"], body["boosts_to_next_level"]) == (next_lvl, missing)


async def test_ca2_en_el_nivel_maximo_no_hay_siguiente(client, as_user, sessions, members):
    await seed_boosts(sessions, 42)
    body = (await info(client, as_user)).json()
    assert (body["level"], body["next_level"], body["boosts_to_next_level"]) == (3, None, 0)


async def test_el_progreso_se_actualiza_al_boostear_y_retirar(client, as_user, sessions, members, give_points):
    await seed_boosts(sessions, 4)
    await give_points("u1", 500)

    boosted = await client.post(f"{INFO}", headers=as_user("u1", idempotency_key=str(uuid.uuid4())))
    after_boost = (await info(client, as_user)).json()
    await client.delete(f"/api/v1/boosts/{boosted.json()['boost']['id']}", headers=as_user("u1"))
    after_removal = (await info(client, as_user)).json()

    assert (after_boost["level"], after_boost["next_level"], after_boost["boosts_to_next_level"]) == (1, 2, 10)
    assert (after_removal["level"], after_removal["next_level"], after_removal["boosts_to_next_level"]) == (0, 1, 1)


async def test_muestra_mis_boosts_en_el_servidor(client, as_user, members, give_points):
    await give_points("u1", 1000)
    for _ in range(2):
        await client.post(INFO, headers=as_user("u1", idempotency_key=str(uuid.uuid4())))

    mine = (await info(client, as_user, user="u1")).json()
    theirs = (await info(client, as_user, user="u2")).json()

    assert (mine["my_active_boosts"], mine["active_boosts"]) == (2, 2)
    assert (theirs["my_active_boosts"], theirs["active_boosts"]) == (0, 2)
    assert mine["boost_cost_points"] == 500


# ── Membresía ──


async def test_un_no_miembro_no_ve_los_boosts(client, as_user, members):
    response = await info(client, as_user, user="intruso")
    assert response.status_code == 403
    assert response.json()["code"] == "not_a_member"


async def test_un_servidor_que_no_existe_responde_404(client, as_user, members):
    response = await info(client, as_user, server=OTHER_SERVER)
    assert response.status_code == 404


async def test_si_server_roles_no_responde_responde_503(client, as_user, members):
    members.available = False
    response = await info(client, as_user)
    assert response.status_code == 503


async def test_sin_usuario_responde_401(client):
    response = await client.get(INFO)
    assert response.status_code == 401
