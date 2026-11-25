"""
Historia: Ver beneficios de servidor boosteado (ADR-001 §2.7, §2.10).

Esqueleto: se implementa junto con la historia (sacar el skip de abajo).
"""

import pytest

pytestmark = pytest.mark.skip(reason="Pendiente: historia Ver beneficios de servidor boosteado")


# ── CA 1: Ver nivel y beneficios actuales ──


async def test_ca1_nivel_y_beneficios_desbloqueados(client, as_user, server_roles):
    """
    Dado un servidor con 15 boosts (nivel 2)
    Cuando un miembro consulta sus boosts
    Entonces ve nivel 2 y los beneficios de los niveles 1 y 2 (emojis +20, ícono animado, banner, adjuntos de
    25 MB), cada uno con su nivel
    """


async def test_ca1_un_servidor_sin_boosts_esta_en_nivel_0(client, as_user, server_roles):
    """Sin boosts (sin fila en server_boost_counters): nivel 0, sin beneficios, 0 boosts (no 404)."""


async def test_ca1_muestra_todos_los_niveles_con_lo_que_desbloquea_cada_uno(client, as_user, server_roles):
    """La respuesta trae los tres niveles con su umbral y sus beneficios, marcando cuáles están desbloqueados."""


# ── CA 2: Ver progreso al siguiente nivel ──


@pytest.mark.parametrize(
    "boosts, next_level, missing",
    [(0, 1, 5), (3, 1, 2), (5, 2, 10), (14, 2, 1), (15, 3, 15), (29, 3, 1)],
)
async def test_ca2_cuantos_boosts_faltan_para_el_siguiente_nivel(
    client, as_user, server_roles, boosts, next_level, missing
):
    """Con N boosts, el siguiente nivel y cuántos faltan."""


async def test_ca2_en_el_nivel_maximo_no_hay_siguiente(client, as_user, server_roles):
    """Con 30 o más boosts: nivel 3, `next_level: null`, `missing: 0`."""


async def test_muestra_mis_boosts_en_el_servidor(client, as_user, server_roles):
    """El miembro ve cuántos de los boosts son suyos (para poder retirarlos)."""


# ── Membresía ──


async def test_un_no_miembro_no_ve_los_boosts(client, as_user, server_roles):
    """Un usuario que no es miembro: 403."""
