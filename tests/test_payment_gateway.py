"""
Pasarela de pago simulada (ADR-001 §2.5). Tests unitarios, sin base.

Esqueleto: se implementa junto con la historia Comprar puntos (sacar el skip de abajo).
"""

import pytest

pytestmark = pytest.mark.skip(reason="Pendiente: pasarela simulada (historia Comprar puntos)")


async def test_la_tarjeta_4242_se_aprueba_con_un_comprobante_unico():
    """charge() aprueba y devuelve `sim_<uuid>`; dos cobros dan comprobantes distintos."""


@pytest.mark.parametrize(
    "card, reason", [("4000000000000002", "card_declined"), ("4000000000009995", "insufficient_funds")]
)
async def test_las_tarjetas_de_rechazo_se_rechazan_con_su_motivo(card, reason):
    """charge() no aprueba, no da comprobante y devuelve el motivo."""


async def test_una_tarjeta_desconocida_se_rechaza():
    """Cualquier otro número: rechazado (`card_declined`). Nunca se aprueba por defecto."""


async def test_el_numero_de_tarjeta_no_queda_en_el_resultado():
    """ChargeResult no guarda la tarjeta (ni en repr): no se persisten datos de tarjeta."""
