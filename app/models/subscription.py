import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.wallet import USER_ID_LENGTH

SUBSCRIPTION_PLANS = ("monthly", "yearly")
# active: pago y vigente. past_due: falló la renovación (se reintenta). canceled: no se renueva, pero sigue
# vigente hasta current_period_end. expired: terminó.
SUBSCRIPTION_STATUSES = ("active", "past_due", "canceled", "expired")


class Subscription(Base):
    """
    Plan Premium de un usuario. Las renovaciones actualizan el período de la misma fila; una suscripción nueva
    después de que la anterior venció es otra fila. A lo sumo una viva (active o past_due) por usuario.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(f"plan IN {SUBSCRIPTION_PLANS}", name="plan_valid"),
        CheckConstraint(f"status IN {SUBSCRIPTION_STATUSES}", name="status_valid"),
        CheckConstraint("current_period_end > current_period_start", name="period_valid"),
        Index(
            "uq_subscriptions_live_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'past_due')"),
        ),
        Index("ix_subscriptions_user_id", "user_id"),
        # El job de renovaciones busca las que vencen
        Index("ix_subscriptions_status_current_period_end", "status", "current_period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(USER_ID_LENGTH), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Comprobante del último cobro (alta o renovación)
    last_payment_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
