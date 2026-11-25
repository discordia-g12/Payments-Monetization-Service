"""
Worker del outbox (ADR-001 §2.8): publica outbox_events en el exchange `payments.events` de RabbitMQ.

Esqueleto: se implementa junto con el worker (sacar el skip de abajo). RabbitMQ se reemplaza por un exchange
falso que guarda lo publicado.
"""

import pytest

pytestmark = pytest.mark.skip(reason="Pendiente: worker del outbox")


async def test_publica_los_pendientes_en_orden_y_los_marca(db_session):
    """
    Tres eventos pendientes se publican en orden de created_at, con routing key = event_type, y quedan con
    processed_at
    """


async def test_el_message_id_es_el_id_del_evento(db_session):
    """El consumidor deduplica por message_id: tiene que ser el id del evento y ser persistente (delivery_mode 2)."""


async def test_si_rabbitmq_falla_el_evento_queda_pendiente(db_session):
    """Si publish lanza, el evento sigue sin processed_at y se reintenta en la próxima vuelta."""


async def test_no_vuelve_a_publicar_los_ya_procesados(db_session):
    """Los que tienen processed_at no se publican de nuevo."""
