"""Transactional Outbox (ADR-001 §2.8): el evento se guarda en la misma transacción que el cambio."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent


def add_event(
    session: AsyncSession, event_type: str, aggregate_type: str, aggregate_id: str, data: dict[str, Any]
) -> OutboxEvent:
    """
    Agrega el evento a la sesión: se guarda con el commit de quien llama, o no se guarda. `event_type` es la
    routing key en el exchange `payments.events` (p. ej. `points.purchased`).
    """
    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload={"event": event_type, "occurred_at": datetime.now(UTC).isoformat(), **data},
    )
    session.add(event)
    return event
