import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.wallet import USER_ID_LENGTH

# pending: registrada, el cobro no se confirmó (en curso, o se cortó a mitad: la concilia el worker).
# completed: cobrada y acreditada. failed: rechazada, o nunca se cobró. refunded: se cobró pero no se pudo
# acreditar, y se devolvió (la compensación de la saga, ADR-001 §2.11).
PURCHASE_STATUSES = ("pending", "completed", "failed", "refunded")


class PointPurchase(Base):
    """
    Una compra de puntos, de punta a punta: es el estado de la saga de compra (ADR-001 §2.11) y también su
    idempotencia (una por usuario e Idempotency-Key). Se crea en `pending` antes de cobrar, así un corte entre
    el cobro y la acreditación deja rastro y el worker de conciliación lo termina o lo devuelve.
    """

    __tablename__ = "point_purchases"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_point_purchases_user_id_idempotency_key"),
        CheckConstraint(f"status IN {PURCHASE_STATUSES}", name="status_valid"),
        CheckConstraint("points > 0", name="points_positive"),
        CheckConstraint("price_cents > 0", name="price_positive"),
        CheckConstraint(
            "status <> 'completed' OR (payment_reference IS NOT NULL AND completed_at IS NOT NULL)",
            name="completed_details",
        ),
        CheckConstraint("status <> 'failed' OR failure_reason IS NOT NULL", name="failed_has_reason"),
        CheckConstraint("status <> 'refunded' OR payment_reference IS NOT NULL", name="refunded_has_payment"),
        # Lo que busca el worker de conciliación: las pendientes, de la más vieja a la más nueva
        Index("ix_point_purchases_pending_updated_at", "updated_at", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(USER_ID_LENGTH), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    package_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Copiados del catálogo al crearla: si el catálogo cambia, la compra conserva lo que se pagó
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    # Comprobante del cobro en la pasarela (nunca datos de la tarjeta)
    payment_reference: Mapped[str | None] = mapped_column(String(128))
    # card_declined, insufficient_funds, not_charged...
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
