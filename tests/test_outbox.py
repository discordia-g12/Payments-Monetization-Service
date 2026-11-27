"""
Publicador del outbox (ADR-001 §2.8). Contra PostgreSQL; RabbitMQ se reemplaza por un exchange falso que guarda lo
publicado.
"""

import asyncio
import json

import aio_pika
import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from app.repositories import outbox
from app.workers import outbox_publisher
from app.workers.outbox_publisher import publish_pending

APPROVED_CARD = "4242424242424242"


class FakeExchange:
    """Guarda (routing_key, mensaje). `fail_on`: la publicación (desde 1) que lanza, como si RabbitMQ cayera."""

    def __init__(self, fail_on: int | None = None, delay: float = 0) -> None:
        self.published: list[tuple[str, aio_pika.Message]] = []
        self.fail_on = fail_on
        self.delay = delay
        self.attempts = 0

    async def publish(self, message, routing_key):
        self.attempts += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_on == self.attempts:
            raise ConnectionError("RabbitMQ no responde")
        self.published.append((routing_key, message))

    @property
    def routing_keys(self) -> list[str]:
        return [key for key, _ in self.published]


async def add_events(sessions, *event_types: str) -> list[str]:
    """Un evento por transacción (así tienen created_at distintos y un orden definido). Devuelve los ids."""
    ids = []
    for event_type in event_types:
        async with sessions() as session:
            event = outbox.add_event(session, event_type, "test", "agg-1", {"n": event_type})
            await session.commit()
            ids.append(str(event.id))
    return ids


async def pending(db_session) -> list[str]:
    db_session.expire_all()
    result = await db_session.execute(
        select(OutboxEvent.event_type).where(OutboxEvent.processed_at.is_(None)).order_by(OutboxEvent.created_at)
    )
    return list(result.scalars())


async def test_publica_los_pendientes_en_orden_y_los_marca(sessions, db_session):
    await add_events(sessions, "a.uno", "a.dos", "a.tres")
    exchange = FakeExchange()

    published = await publish_pending(sessions, exchange)

    assert published == 3
    assert exchange.routing_keys == ["a.uno", "a.dos", "a.tres"]
    assert await pending(db_session) == []


async def test_el_mensaje_lleva_el_id_del_evento_y_es_persistente(sessions):
    [event_id] = await add_events(sessions, "points.purchased")
    exchange = FakeExchange()

    await publish_pending(sessions, exchange)

    [(routing_key, message)] = exchange.published
    assert routing_key == "points.purchased"
    assert message.message_id == event_id
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.type == "points.purchased"
    assert message.content_type == "application/json"
    body = json.loads(message.body)
    assert (body["event"], body["n"]) == ("points.purchased", "points.purchased")
    assert message.headers["aggregate_id"] == "agg-1"


async def test_si_rabbitmq_falla_el_evento_queda_pendiente(sessions, db_session):
    await add_events(sessions, "a.uno", "a.dos", "a.tres")

    failing = FakeExchange(fail_on=2)
    with pytest.raises(ConnectionError):
        await publish_pending(sessions, failing)

    # El primero salió y quedó marcado; el que falló y los siguientes esperan (en orden)
    assert failing.routing_keys == ["a.uno"]
    assert await pending(db_session) == ["a.dos", "a.tres"]

    retry = FakeExchange()
    assert await publish_pending(sessions, retry) == 2
    assert retry.routing_keys == ["a.dos", "a.tres"]


async def test_no_vuelve_a_publicar_los_ya_procesados(sessions):
    await add_events(sessions, "a.uno")
    await publish_pending(sessions, FakeExchange())

    again = FakeExchange()
    assert await publish_pending(sessions, again) == 0
    assert again.published == []


async def test_publica_de_a_tandas(sessions, db_session):
    await add_events(sessions, "e.1", "e.2", "e.3", "e.4", "e.5")
    exchange = FakeExchange()

    assert await publish_pending(sessions, exchange, batch_size=2) == 2
    assert exchange.routing_keys == ["e.1", "e.2"]
    assert len(await pending(db_session)) == 3


async def test_dos_publicadores_a_la_vez_no_publican_dos_veces_el_mismo(sessions):
    """FOR UPDATE SKIP LOCKED: cada evento lo toma uno solo."""
    ids = await add_events(sessions, "e.1", "e.2", "e.3", "e.4", "e.5", "e.6")
    first, second = FakeExchange(delay=0.01), FakeExchange(delay=0.01)

    await asyncio.gather(publish_pending(sessions, first), publish_pending(sessions, second))

    message_ids = [m.message_id for _, m in first.published + second.published]
    assert sorted(message_ids) == sorted(ids)


async def test_una_compra_publica_sus_eventos(client, as_user, sessions):
    await client.post(
        "/api/v1/points/purchases",
        json={"package_id": "points_500", "card_number": APPROVED_CARD},
        headers=as_user("u1", idempotency_key="k1"),
    )
    exchange = FakeExchange()

    await publish_pending(sessions, exchange)

    assert sorted(exchange.routing_keys) == ["payment.completed", "points.purchased"]


async def test_sin_rabbitmq_url_no_publica_ni_se_cuelga(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    # Vuelve enseguida (si intentara conectarse, el test se colgaría o fallaría)
    await asyncio.wait_for(outbox_publisher.run_forever(), timeout=1)


def test_como_contenedor_sin_rabbitmq_url_falla_y_lo_dice(monkeypatch, caplog):
    """El worker no queda "sano" sin hacer nada: sale con error (y el contenedor se ve caído)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/payments_db")
    monkeypatch.delenv("RABBITMQ_URL", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        outbox_publisher.main()

    assert exit_info.value.code == 1
    assert "RABBITMQ_URL" in caplog.text
