"""
Publicador del Transactional Outbox (ADR-001 §2.8): manda los eventos de outbox_events al exchange
`payments.events` (topic) de RabbitMQ.

- Al menos una vez: un evento se marca como publicado DESPUÉS de que RabbitMQ lo confirma. Si el proceso se corta
  en el medio, se vuelve a publicar; los consumidores deduplican por message_id (el id del evento).
- Orden: dentro de cada vuelta, por created_at. Un evento que falla corta la vuelta, así no sale uno posterior
  antes que él.
- `FOR UPDATE SKIP LOCKED`: si hay dos publicadores (dos réplicas del worker), cada evento lo toma uno solo.

Corre en su propio contenedor (`payments-outbox-worker`, misma imagen que la API), como los de Auth y Server-Roles:
`python -m app.workers.outbox_publisher`. Toca /tmp/outbox-heartbeat después de cada vuelta sana (incluye estar
conectado a RabbitMQ); el healthcheck del contenedor falla si el archivo tiene más de 30 s.
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import aio_pika
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import OutboxEvent

logger = logging.getLogger("payments.outbox")

RECONNECT_DELAY_SECONDS = 5
HEARTBEAT = Path("/tmp/outbox-heartbeat")  # nosec B108 # archivo de liveness dentro del contenedor


class Exchange(Protocol):
    async def publish(self, message: aio_pika.abc.AbstractMessage, routing_key: str) -> object: ...


def build_message(event: OutboxEvent) -> aio_pika.Message:
    return aio_pika.Message(
        body=json.dumps(event.payload, default=str).encode(),
        content_type="application/json",
        # Sin esto el mensaje es transitorio: si RabbitMQ se reinicia, se pierde aunque la cola sea durable
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=str(event.id),  # clave de deduplicación para los consumidores
        type=event.event_type,
        timestamp=event.created_at,
        headers={"aggregate_type": event.aggregate_type, "aggregate_id": event.aggregate_id},
    )


async def publish_pending(
    sessions: async_sessionmaker[AsyncSession], exchange: Exchange, batch_size: int = 50
) -> int:
    """
    Publica una tanda de eventos pendientes, en orden, y los marca. Devuelve cuántos publicó. Si uno falla, los
    anteriores quedan marcados y el error se relanza (ese y los siguientes salen en la próxima vuelta).
    """
    published = 0
    failure: Exception | None = None
    async with sessions() as session, session.begin():
        result = await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None))
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        for event in result.scalars():
            try:
                # Con publisher confirms (default de aio-pika), esto vuelve cuando RabbitMQ lo aceptó
                await exchange.publish(build_message(event), routing_key=event.event_type)
            except Exception as exc:
                failure = exc
                break
            event.processed_at = datetime.now(UTC)
            published += 1
            logger.info("Publicado %s (%s) -> %s", event.event_type, event.id, event.aggregate_id)
    # Se confirma lo publicado aunque haya fallado uno: si no, se volvería a mandar todo
    if failure is not None:
        raise failure
    return published


async def run_forever(stop: asyncio.Event | None = None) -> None:
    """Conecta a RabbitMQ y publica cada `outbox_poll_interval_seconds` hasta que se pida parar."""
    settings = get_settings()
    stop = stop or asyncio.Event()
    if not settings.rabbitmq_url:
        logger.warning("Sin RABBITMQ_URL: no se publica el outbox (los eventos esperan en outbox_events)")
        return

    while not stop.is_set():
        try:
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        except Exception as exc:
            logger.warning("No se pudo conectar a RabbitMQ (%s); se reintenta en %ss", exc, RECONNECT_DELAY_SECONDS)
            await _wait(stop, RECONNECT_DELAY_SECONDS)
            continue

        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                settings.rabbitmq_exchange, aio_pika.ExchangeType.TOPIC, durable=True
            )
            logger.info("Outbox conectado a RabbitMQ, exchange=%s", settings.rabbitmq_exchange)
            while not stop.is_set():
                try:
                    # Mientras haya tandas llenas, sigue sin esperar
                    while await publish_pending(get_sessionmaker(), exchange, settings.outbox_batch_size) >= (
                        settings.outbox_batch_size
                    ):
                        pass
                    HEARTBEAT.touch()
                except Exception:
                    logger.exception(
                        "Vuelta del outbox fallida; se reintenta en %ss", settings.outbox_poll_interval_seconds
                    )
                await _wait(stop, settings.outbox_poll_interval_seconds)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    # Como contenedor, sin configuración no tiene sentido quedar "sano" sin hacer nada: falla y lo dice
    required = {"DATABASE_URL": settings.database_url, "RABBITMQ_URL": settings.rabbitmq_url}
    missing = [name for name, value in required.items() if not value]
    if missing:
        logger.error("Falta %s: el publicador del outbox no puede arrancar", ", ".join(missing))
        sys.exit(1)
    asyncio.run(_run_until_signal())


async def _run_until_signal() -> None:
    """docker stop manda SIGTERM: termina la tanda en curso y sale, en vez de cortar a mitad de una publicación."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_forever(stop)


if __name__ == "__main__":
    main()
