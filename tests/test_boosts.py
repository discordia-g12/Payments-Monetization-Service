"""
Historia: Usar puntos para boostear un servidor (ADR-001 §2.3, §2.7, §2.9).

Un boost cuesta 500 puntos. Niveles: 1 con 5 boosts, 2 con 15, 3 con 30. Esqueleto: se implementa junto con la
historia (sacar el skip de abajo).
"""

import pytest

pytestmark = pytest.mark.skip(reason="Pendiente: historia Usar puntos para boostear un servidor")

BOOST_COST = 500


# ── CA 1: Boost exitoso ──


async def test_ca1_boost_descuenta_los_puntos_y_suma_al_servidor(client, as_user, db_session, server_roles):
    """
    Dado un miembro con 1000 puntos
    Cuando boostea el servidor
    Entonces responde 201, le quedan 500, el servidor tiene 1 boost vigente, hay un server_boosts con
    points_spent = 500 y un point_transaction `boost` de -500 que lo apunta
    """


async def test_ca1_un_miembro_puede_boostear_varias_veces(client, as_user, server_roles):
    """Tres boosts del mismo miembro son tres unidades independientes, cada una de 500."""


async def test_ca1_el_boost_deja_server_boosted_en_el_outbox(client, as_user, db_session, server_roles):
    """Cada boost deja un `server.boosted` con el servidor, el usuario y el id del boost."""


# ── CA 2: Saldo insuficiente ──


async def test_ca2_sin_saldo_suficiente_no_boostea_e_informa_lo_que_falta(client, as_user, db_session, server_roles):
    """
    Dado un miembro con 300 puntos
    Cuando intenta boostear
    Entonces responde 409 (o 402) con `missing_points: 200`, el saldo sigue en 300 y no hay boost
    """


async def test_ca2_sin_billetera_falta_el_costo_entero(client, as_user, server_roles):
    """Un usuario que nunca compró: faltan 500."""


# ── CA 3: El servidor alcanza un nuevo nivel ──


@pytest.mark.parametrize("boosts, level", [(4, 0), (5, 1), (14, 1), (15, 2), (29, 2), (30, 3), (40, 3)])
async def test_ca3_el_nivel_sale_de_los_boosts_vigentes(client, as_user, server_roles, boosts, level):
    """Con N boosts vigentes, el servidor está en el nivel correspondiente."""


async def test_ca3_el_boost_que_cruza_el_umbral_sube_el_nivel_y_lo_avisa(client, as_user, db_session, server_roles):
    """
    Dado un servidor con 4 boosts
    Cuando se registra el quinto
    Entonces la respuesta dice nivel 1 y hay un `server.boost_level_changed` (0 → 1) en el outbox
    """


async def test_ca3_boosts_que_no_cruzan_umbral_no_avisan_cambio_de_nivel(client, as_user, db_session, server_roles):
    """Del boost 6 al 14 no hay `server.boost_level_changed`."""


async def test_ca3_boosts_simultaneos_cruzan_el_umbral_una_sola_vez(client, as_user, db_session, server_roles):
    """
    Con 4 boosts, dos miembros boostean a la vez: el servidor queda con 6, nivel 1, y hay UN solo
    `server.boost_level_changed` (FOR UPDATE sobre server_boost_counters)
    """


# ── CA 4: Retirar boost propio ──


async def test_ca4_retirar_resta_del_servidor_sin_reembolso(client, as_user, db_session, server_roles):
    """
    Dado un miembro que boosteó
    Cuando retira su boost
    Entonces responde 200, el servidor tiene un boost menos, el saldo no cambia, el boost queda con removed_at
    y hay un `server.boost_removed` en el outbox
    """


async def test_ca4_retirar_por_debajo_del_umbral_baja_el_nivel(client, as_user, db_session, server_roles):
    """Con 5 boosts (nivel 1), retirar uno lo deja en 4, nivel 0, con `server.boost_level_changed` (1 → 0)."""


async def test_ca4_no_se_puede_retirar_el_boost_de_otro(client, as_user, server_roles):
    """Retirar un boost ajeno responde 404 (no se revela que existe) y no cambia nada."""


async def test_ca4_retirar_dos_veces_el_mismo_boost(client, as_user, server_roles):
    """El segundo retiro responde 404 o 409 y el contador no baja dos veces."""


# ── Membresía (ADR-001 §2.9) ──


async def test_un_no_miembro_no_puede_boostear(client, as_user, db_session, server_roles):
    """Un usuario con saldo que no es miembro: 403, sin mover puntos."""


async def test_un_servidor_que_no_existe_responde_404(client, as_user, server_roles):
    """Server-Roles no conoce el servidor: 404."""


async def test_si_server_roles_no_responde_no_se_boostea(client, as_user, db_session, server_roles):
    """Server-Roles caído: 503, sin mover puntos (nunca se boostea sin verificar la membresía)."""


# ── Idempotencia y concurrencia ──


async def test_reintentar_el_boost_con_la_misma_clave_no_cobra_dos_veces(client, as_user, db_session, server_roles):
    """Dos POST con la misma Idempotency-Key: un solo boost y un solo débito."""


async def test_boosts_simultaneos_del_mismo_usuario_no_dejan_saldo_negativo(client, as_user, db_session, server_roles):
    """Con 1000 puntos, tres boosts en paralelo: dos salen bien, uno da saldo insuficiente, y el saldo queda en 0."""
