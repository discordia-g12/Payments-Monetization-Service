"""
Saga de compra (ADR-001 §2.11): compensación cuando no se puede acreditar, y conciliación de compras que
quedaron a medias. Contra PostgreSQL.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models import OutboxEvent, PointPurchase, PointTransaction, Wallet
from app.services import points
from app.services.catalog import get_package

APPROVED_CARD = "4242424242424242"
LATER = datetime.now(UTC) + timedelta(hours=1)


async def buy(client, as_user, key="k1", card=APPROVED_CARD):
    return await client.post(
        "/api/v1/points/purchases",
        json={"package_id": "points_500", "card_number": card},
        headers=as_user("u1", idempotency_key=key),
    )


async def pending_purchase(sessions, key="k1") -> PointPurchase:
    """Una compra registrada (paso 1) que no llegó a cobrarse ni a acreditarse: como si se cortara ahí."""
    purchase, created = await points._start(sessions, "u1", key, get_package("points_500"))
    assert created
    return purchase


async def charge(gateway, purchase: PointPurchase, card=APPROVED_CARD):
    """El cobro del paso 2, hecho en la pasarela (con el id de la compra como clave)."""
    return await gateway.charge(
        amount_cents=purchase.price_cents, currency="USD", card_number=card, idempotency_key=str(purchase.id)
    )


async def reload(db_session, purchase: PointPurchase) -> PointPurchase:
    db_session.expire_all()
    return await db_session.get(PointPurchase, purchase.id)


# ── Compensación: se cobró pero no se pudo acreditar ──


async def test_si_falla_la_acreditacion_se_devuelve_el_cobro(client, as_user, db_session, gateway, monkeypatch):
    async def broken_complete(*args, **kwargs):
        raise RuntimeError("se cayó la base justo al acreditar")

    monkeypatch.setattr(points, "_complete", broken_complete)

    response = await buy(client, as_user)

    assert response.status_code == 503
    assert response.json()["code"] == "purchase_refunded"
    assert len(gateway.refunds) == 1
    purchase = (await db_session.execute(select(PointPurchase))).scalar_one()
    assert purchase.status == "refunded"
    assert purchase.payment_reference == gateway.refunds[0]
    assert await db_session.get(Wallet, "u1") is None
    assert list((await db_session.execute(select(OutboxEvent.event_type))).scalars()) == ["payment.refunded"]


async def test_si_tampoco_se_puede_devolver_queda_pendiente_para_conciliar(
    client, as_user, db_session, gateway, monkeypatch
):
    async def broken_complete(*args, **kwargs):
        raise RuntimeError("se cayó la base")

    monkeypatch.setattr(points, "_complete", broken_complete)
    gateway.refund_fails = True

    response = await buy(client, as_user)

    assert response.status_code == 503
    assert response.json()["code"] == "payment_pending"
    purchase = (await db_session.execute(select(PointPurchase))).scalar_one()
    assert purchase.status == "pending"


async def test_reintentar_una_compra_devuelta_responde_lo_mismo(client, as_user, gateway, monkeypatch):
    original = points._complete

    async def broken_complete(*args, **kwargs):
        raise RuntimeError("se cayó la base")

    monkeypatch.setattr(points, "_complete", broken_complete)
    await buy(client, as_user, key="k1")
    monkeypatch.setattr(points, "_complete", original)

    again = await buy(client, as_user, key="k1")

    assert again.status_code == 503
    assert again.json()["code"] == "purchase_refunded"
    assert gateway.charge_calls == 1


# ── Conciliación de compras pendientes ──


async def test_conciliar_acredita_una_compra_cobrada_que_no_se_llego_a_acreditar(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)
    result = await charge(gateway, purchase)

    closed = await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    assert closed == 1
    purchase = await reload(db_session, purchase)
    assert (purchase.status, purchase.payment_reference) == ("completed", result.reference)
    assert (await db_session.get(Wallet, "u1")).balance == 500


async def test_conciliar_da_por_fallida_una_compra_que_nunca_se_cobro(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)

    await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    purchase = await reload(db_session, purchase)
    assert (purchase.status, purchase.failure_reason) == ("failed", "not_charged")
    assert await db_session.get(Wallet, "u1") is None


async def test_conciliar_marca_fallida_una_compra_rechazada(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)
    await charge(gateway, purchase, card="4000000000009995")

    await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    purchase = await reload(db_session, purchase)
    assert (purchase.status, purchase.failure_reason) == ("failed", "insufficient_funds")


async def test_conciliar_marca_devuelta_una_compra_cuyo_cobro_se_devolvio(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)
    result = await charge(gateway, purchase)
    await gateway.refund(result.reference)

    await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    purchase = await reload(db_session, purchase)
    assert purchase.status == "refunded"
    assert await db_session.get(Wallet, "u1") is None


async def test_conciliar_no_toca_las_compras_recientes(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)
    await charge(gateway, purchase)

    closed = await points.reconcile_pending_purchases(sessions, gateway)  # ahora: la compra es de recién

    assert closed == 0
    assert (await reload(db_session, purchase)).status == "pending"


async def test_conciliar_dos_veces_no_acredita_dos_veces(sessions, db_session, gateway):
    purchase = await pending_purchase(sessions)
    await charge(gateway, purchase)

    await points.reconcile_pending_purchases(sessions, gateway, now=LATER)
    await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    assert (await db_session.get(Wallet, "u1")).balance == 500
    assert await db_session.scalar(select(func.count()).select_from(PointTransaction)) == 1


async def test_si_la_pasarela_no_responde_la_compra_sigue_pendiente(sessions, db_session, gateway, monkeypatch):
    purchase = await pending_purchase(sessions)

    async def unavailable(_key):
        raise ConnectionError("la pasarela no responde")

    monkeypatch.setattr(gateway, "find_charge", unavailable)

    closed = await points.reconcile_pending_purchases(sessions, gateway, now=LATER)

    assert closed == 0
    assert (await reload(db_session, purchase)).status == "pending"
