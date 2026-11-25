"""Historia: Comprar puntos (ADR-001 §2.2, §2.4 a §2.6 y la saga de §2.11). Contra PostgreSQL."""

import asyncio

import pytest
from sqlalchemy import func, select

from app.models import OutboxEvent, PointPurchase, PointTransaction, Wallet

APPROVED_CARD = "4242424242424242"
DECLINED_CARD = "4000000000000002"
INSUFFICIENT_FUNDS_CARD = "4000000000009995"
PURCHASES = "/api/v1/points/purchases"
WALLET = "/api/v1/points/wallet"


async def buy(client, as_user, user="u1", key="k1", package="points_500", card=APPROVED_CARD):
    return await client.post(
        PURCHASES, json={"package_id": package, "card_number": card}, headers=as_user(user, idempotency_key=key)
    )


async def count(db_session, model, *where) -> int:
    return await db_session.scalar(select(func.count()).select_from(model).where(*where))


async def events(db_session) -> list[str]:
    """Tipos de evento del outbox, ordenados por nombre (los de una misma transacción tienen el mismo created_at)."""
    rows = await db_session.execute(select(OutboxEvent.event_type))
    return sorted(rows.scalars())


# ── CA 1: Compra exitosa ──


async def test_ca1_compra_aprobada_acredita_los_puntos_del_paquete(client, as_user, db_session):
    response = await buy(client, as_user)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["balance"] == 500
    assert body["purchase"]["status"] == "completed"
    assert body["purchase"]["points"] == 500
    assert body["purchase"]["price_cents"] == 499
    assert body["purchase"]["payment_reference"].startswith("sim_")

    wallet = await db_session.get(Wallet, "u1")
    assert wallet.balance == 500
    movement = (await db_session.execute(select(PointTransaction))).scalar_one()
    assert (movement.kind, movement.amount, movement.balance_after) == ("purchase", 500, 500)
    assert (movement.package_id, movement.price_cents, movement.currency) == ("points_500", 499, "USD")
    assert movement.payment_reference == body["purchase"]["payment_reference"]


async def test_ca1_la_compra_suma_al_saldo_que_ya_tenia(client, as_user):
    await buy(client, as_user, key="k1", package="points_500")
    response = await buy(client, as_user, key="k2", package="points_1000")

    assert response.json()["balance"] == 1500
    movements = (await client.get(WALLET, headers=as_user("u1"))).json()["movements"]
    assert [m["balance_after"] for m in movements] == [1500, 500]


async def test_ca1_la_compra_deja_eventos_en_el_outbox(client, as_user, db_session):
    await buy(client, as_user)

    assert await events(db_session) == ["payment.completed", "points.purchased"]
    purchased = (
        await db_session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "points.purchased"))
    ).scalar_one()
    assert purchased.payload["user_id"] == "u1"
    assert purchased.payload["points"] == 500
    assert purchased.payload["balance"] == 500


async def test_el_paquete_tiene_que_existir(client, as_user, db_session):
    response = await buy(client, as_user, package="points_999999")

    assert response.status_code == 422
    assert response.json()["code"] == "unknown_package"
    assert await count(db_session, PointPurchase) == 0


async def test_el_cliente_no_puede_mandar_puntos_ni_precio(client, as_user):
    response = await client.post(
        PURCHASES,
        json={"package_id": "points_500", "card_number": APPROVED_CARD, "points": 99999},
        headers=as_user("u1", idempotency_key="k1"),
    )
    assert response.status_code == 422


async def test_los_paquetes_son_los_de_la_web(client, as_user):
    packages = (await client.get("/api/v1/points/packages", headers=as_user("u1"))).json()["packages"]
    assert [(p["id"], p["points"], p["price_cents"]) for p in packages] == [
        ("points_500", 500, 499),
        ("points_1000", 1000, 949),
        ("points_2500", 2500, 2199),
        ("points_5000", 5000, 3999),
    ]


# ── CA 2: Pago rechazado ──


@pytest.mark.parametrize(
    "card, reason", [(DECLINED_CARD, "card_declined"), (INSUFFICIENT_FUNDS_CARD, "insufficient_funds")]
)
async def test_ca2_pago_rechazado_no_acredita_e_informa_el_motivo(client, as_user, db_session, card, reason):
    await buy(client, as_user, key="primera")

    response = await buy(client, as_user, key="rechazada", card=card)

    assert response.status_code == 402
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "payment_declined"
    assert response.json()["reason"] == reason
    assert (await db_session.get(Wallet, "u1")).balance == 500
    assert await count(db_session, PointTransaction) == 1
    purchase = await db_session.scalar(select(PointPurchase).where(PointPurchase.idempotency_key == "rechazada"))
    assert (purchase.status, purchase.failure_reason) == ("failed", reason)


async def test_ca2_el_rechazo_deja_payment_failed_en_el_outbox(client, as_user, db_session):
    await buy(client, as_user, card=DECLINED_CARD)
    assert await events(db_session) == ["payment.failed"]


async def test_si_la_pasarela_no_responde_no_se_acredita_y_queda_pendiente(client, as_user, db_session, gateway):
    gateway.unavailable = True

    response = await buy(client, as_user)

    assert response.status_code == 503
    assert response.json()["code"] == "payment_pending"
    assert await count(db_session, Wallet) == 0
    purchase = (await db_session.execute(select(PointPurchase))).scalar_one()
    assert purchase.status == "pending"


# ── CA 3: Ver saldo e historial ──


async def test_ca3_saldo_e_historial_de_compras(client, as_user):
    await buy(client, as_user, key="k1", package="points_500")
    await buy(client, as_user, key="k2", package="points_2500")

    response = await client.get(WALLET, headers=as_user("u1"))

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 3000
    assert [(m["kind"], m["package_id"], m["amount"], m["price_cents"]) for m in body["movements"]] == [
        ("purchase", "points_2500", 2500, 2199),
        ("purchase", "points_500", 500, 499),
    ]
    assert body["next_cursor"] is None


async def test_ca3_un_usuario_sin_compras_ve_saldo_cero(client, as_user):
    response = await client.get(WALLET, headers=as_user("nuevo"))
    assert response.status_code == 200
    assert response.json() == {"balance": 0, "movements": [], "next_cursor": None}


async def test_ca3_el_historial_se_pagina(client, as_user):
    for i in range(5):
        await buy(client, as_user, key=f"k{i}")

    first = (await client.get(WALLET, params={"limit": 2}, headers=as_user("u1"))).json()
    second = (
        await client.get(WALLET, params={"limit": 2, "before": first["next_cursor"]}, headers=as_user("u1"))
    ).json()
    last = (
        await client.get(WALLET, params={"limit": 2, "before": second["next_cursor"]}, headers=as_user("u1"))
    ).json()

    assert [m["balance_after"] for m in first["movements"]] == [2500, 2000]
    assert [m["balance_after"] for m in second["movements"]] == [1500, 1000]
    assert [m["balance_after"] for m in last["movements"]] == [500]
    assert last["next_cursor"] is None


async def test_cada_usuario_ve_solo_su_billetera(client, as_user):
    await buy(client, as_user, user="u1")
    response = await client.get(WALLET, headers=as_user("u2"))
    assert response.json()["balance"] == 0


async def test_sin_usuario_responde_401(client):
    response = await client.get(WALLET)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


# ── Idempotencia (ADR-001 §2.4) ──


async def test_reintentar_con_la_misma_clave_no_cobra_dos_veces(client, as_user, db_session, gateway):
    first = await buy(client, as_user, key="mismo-intento")
    again = await buy(client, as_user, key="mismo-intento")

    assert again.status_code == 201
    assert again.json()["purchase"]["id"] == first.json()["purchase"]["id"]
    assert again.json()["balance"] == 500
    assert gateway.charge_calls == 1
    assert await count(db_session, PointTransaction) == 1


async def test_reintentar_un_rechazo_devuelve_el_mismo_rechazo(client, as_user, gateway):
    await buy(client, as_user, key="k1", card=DECLINED_CARD)
    again = await buy(client, as_user, key="k1", card=DECLINED_CARD)

    assert again.status_code == 402
    assert again.json()["reason"] == "card_declined"
    assert gateway.charge_calls == 1


async def test_la_misma_clave_con_otro_paquete_responde_422(client, as_user):
    await buy(client, as_user, key="k1", package="points_500")
    response = await buy(client, as_user, key="k1", package="points_1000")

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


async def test_comprar_sin_idempotency_key_responde_400(client, as_user, db_session):
    response = await client.post(
        PURCHASES, json={"package_id": "points_500", "card_number": APPROVED_CARD}, headers=as_user("u1")
    )
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"
    assert await count(db_session, PointPurchase) == 0


async def test_la_clave_es_por_usuario(client, as_user):
    one = await buy(client, as_user, user="u1", key="compartida")
    other = await buy(client, as_user, user="u2", key="compartida")

    assert one.status_code == other.status_code == 201
    assert one.json()["purchase"]["id"] != other.json()["purchase"]["id"]


async def test_el_numero_de_tarjeta_no_vuelve_en_los_errores(client, as_user):
    response = await client.post(
        PURCHASES,
        json={"package_id": "points_500", "card_number": "4242-4242-4242-4242"},
        headers=as_user("u1", idempotency_key="k1"),
    )
    assert response.status_code == 422
    assert "4242" not in response.text


# ── Concurrencia (ADR-001 §2.3) ──


async def test_compras_simultaneas_del_mismo_usuario_suman_bien(client, as_user, db_session):
    responses = await asyncio.gather(*(buy(client, as_user, key=f"k{i}") for i in range(10)))

    assert all(r.status_code == 201 for r in responses)
    assert (await db_session.get(Wallet, "u1")).balance == 5000
    after = list((await db_session.execute(select(PointTransaction.balance_after))).scalars())
    assert sorted(after) == [500 * i for i in range(1, 11)]


async def test_dos_pedidos_simultaneos_con_la_misma_clave_cobran_una_vez(client, as_user, db_session, gateway):
    responses = await asyncio.gather(*(buy(client, as_user, key="la-misma") for _ in range(5)))

    assert {r.status_code for r in responses} <= {201, 409}
    assert gateway.charge_calls == 1
    assert (await db_session.get(Wallet, "u1")).balance == 500
