"""
Historia: Comprar puntos (ADR-001 §2.2, §2.4 a §2.6).

Esqueleto: cada test describe un criterio de aceptación o un caso borde. Se implementan junto con la historia
(sacar el skip de abajo).
"""

import pytest

pytestmark = pytest.mark.skip(reason="Pendiente: historia Comprar puntos")

APPROVED_CARD = "4242424242424242"
DECLINED_CARD = "4000000000000002"
INSUFFICIENT_FUNDS_CARD = "4000000000009995"


# ── CA 1: Compra exitosa ──


async def test_ca1_compra_aprobada_acredita_los_puntos_del_paquete(client, as_user, db_session):
    """
    Dado un usuario sin billetera
    Cuando compra `points_500` con la tarjeta aprobada
    Entonces responde 201 con el saldo (500) y el movimiento, y en la base hay una billetera con 500 y un
    point_transaction `purchase` con el precio (499 USD cents) y el comprobante `sim_...`
    """


async def test_ca1_la_compra_suma_al_saldo_que_ya_tenia(client, as_user):
    """Dos compras (500 + 1000) dejan 1500, y el historial tiene las dos con su balance_after."""


async def test_ca1_la_compra_deja_eventos_en_el_outbox(client, as_user, db_session):
    """Una compra aprobada deja `payment.completed` y `points.purchased` en outbox_events, en la misma transacción."""


async def test_el_cliente_manda_el_paquete_no_el_precio(client, as_user):
    """Un paquete que no existe da 422; mandar `points` o `price` en el cuerpo no cambia lo que se acredita."""


# ── CA 2: Pago rechazado ──


@pytest.mark.parametrize(
    "card, reason", [(DECLINED_CARD, "card_declined"), (INSUFFICIENT_FUNDS_CARD, "insufficient_funds")]
)
async def test_ca2_pago_rechazado_no_acredita_e_informa_el_motivo(client, as_user, db_session, card, reason):
    """
    Dado un usuario con 500 puntos
    Cuando compra con una tarjeta que la pasarela rechaza
    Entonces responde 402 con el motivo, el saldo sigue en 500 y no hay point_transaction nuevo
    """


async def test_ca2_el_rechazo_deja_payment_failed_en_el_outbox(client, as_user, db_session):
    """Un pago rechazado deja `payment.failed` y nada más."""


async def test_si_la_pasarela_falla_no_se_acredita_nada(client, as_user, db_session):
    """Un error de la pasarela (no un rechazo) da 502/503 y no mueve puntos."""


# ── CA 3: Ver saldo e historial ──


async def test_ca3_saldo_e_historial_de_compras(client, as_user):
    """
    Dado un usuario con dos compras
    Cuando pide su billetera
    Entonces ve el total y el historial, del más nuevo al más viejo, con paquete, puntos, precio y fecha
    """


async def test_ca3_un_usuario_sin_compras_ve_saldo_cero(client, as_user):
    """Sin billetera todavía: saldo 0 e historial vacío (no 404)."""


async def test_ca3_el_historial_se_pagina(client, as_user):
    """Con más movimientos que el límite, devuelve una página y el cursor de la siguiente."""


async def test_cada_usuario_ve_solo_su_billetera(client, as_user):
    """El usuario sale de X-User-Id: no hay forma de pedir la billetera de otro."""


async def test_sin_usuario_responde_401(client):
    """Sin X-User-Id (pedido que no pasó por Kong) responde 401."""


# ── Idempotencia (ADR-001 §2.4) ──


async def test_reintentar_con_la_misma_clave_no_cobra_dos_veces(client, as_user, db_session):
    """Dos POST iguales con la misma Idempotency-Key: la misma respuesta, un solo cobro y 500 puntos, no 1000."""


async def test_la_misma_clave_con_otro_paquete_responde_422(client, as_user):
    """Reusar la clave con otro cuerpo es un error del cliente."""


async def test_comprar_sin_idempotency_key_responde_400(client, as_user):
    """La clave es obligatoria en las operaciones que cobran."""


async def test_la_clave_es_por_usuario(client, as_user):
    """La misma clave de dos usuarios distintos son dos compras distintas."""


# ── Concurrencia (ADR-001 §2.3) ──


async def test_compras_simultaneas_del_mismo_usuario_suman_bien(client, as_user, db_session):
    """
    Diez compras en paralelo (asyncio.gather, claves distintas) dejan exactamente 10 x 500 puntos, y cada
    balance_after del historial es distinto y consecutivo (FOR UPDATE sobre la billetera).
    """
