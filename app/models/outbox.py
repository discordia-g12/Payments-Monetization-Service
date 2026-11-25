import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEvent(Base):
    """
    Transactional Outbox (como Auth-User y Server-Roles, ARQUITECTURA.md § Eventos): el evento se guarda en la
    misma transacción que el cambio (PaymentCompleted, PointsPurchased, ServerBoosted, ServerBoostLevelChanged,
    PremiumSubscribed, ...) y un worker aparte lo publica en RabbitMQ. Al menos una vez: los consumidores
    deduplican por `id` (va como message_id).
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        # Lo que el worker busca en cada vuelta: pendientes, en orden de llegada
        Index("ix_outbox_events_pending_created_at", "created_at", postgresql_where=text("processed_at IS NULL")),
        Index("ix_outbox_events_aggregate_id", "aggregate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # También es la routing key en el exchange
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
