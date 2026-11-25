"""Pasarela de pago simulada (ADR-001 §2.5). Tests unitarios, sin base."""

import pytest

from app.services.payment_gateway import PaymentGatewayError, SimulatedPaymentGateway

APPROVED = "4242 4242 4242 4242"


async def charge(gateway: SimulatedPaymentGateway, card: str, key: str = "k1"):
    return await gateway.charge(amount_cents=499, currency="USD", card_number=card, idempotency_key=key)


async def test_la_tarjeta_4242_se_aprueba_con_un_comprobante_unico():
    gateway = SimulatedPaymentGateway()
    first = await charge(gateway, APPROVED, "k1")
    second = await charge(gateway, APPROVED, "k2")

    assert first.approved and second.approved
    assert first.reference.startswith("sim_")
    assert first.reference != second.reference
    assert first.decline_reason is None


@pytest.mark.parametrize(
    "card, reason", [("4000000000000002", "card_declined"), ("4000 0000 0000 9995", "insufficient_funds")]
)
async def test_las_tarjetas_de_rechazo_se_rechazan_con_su_motivo(card, reason):
    result = await charge(SimulatedPaymentGateway(), card)

    assert not result.approved
    assert result.status == "declined"
    assert result.reference is None
    assert result.decline_reason == reason


async def test_una_tarjeta_desconocida_se_rechaza():
    result = await charge(SimulatedPaymentGateway(), "5555555555554444")
    assert result.status == "declined"
    assert result.decline_reason == "card_declined"


async def test_la_misma_clave_no_cobra_dos_veces():
    gateway = SimulatedPaymentGateway()
    first = await charge(gateway, APPROVED, "compra-1")
    # Aunque cambie la tarjeta, con la misma clave devuelve el primer resultado
    again = await charge(gateway, "4000000000000002", "compra-1")
    assert again == first


async def test_devolver_un_cobro_lo_deja_devuelto():
    gateway = SimulatedPaymentGateway()
    result = await charge(gateway, APPROVED, "compra-1")

    await gateway.refund(result.reference)

    found = await gateway.find_charge("compra-1")
    assert found.status == "refunded"
    assert found.reference == result.reference


async def test_devolver_un_cobro_que_no_existe_falla():
    with pytest.raises(PaymentGatewayError):
        await SimulatedPaymentGateway().refund("sim_no_existe")


async def test_find_charge_sin_cobro_devuelve_none():
    assert await SimulatedPaymentGateway().find_charge("nunca-cobrada") is None


async def test_el_numero_de_tarjeta_no_queda_en_el_resultado():
    result = await charge(SimulatedPaymentGateway(), APPROVED)
    assert "4242" not in repr(result)
    assert "4242" not in (result.reference or "")
