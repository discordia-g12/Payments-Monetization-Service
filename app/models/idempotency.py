from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.wallet import USER_ID_LENGTH

IDEMPOTENCY_STATUSES = ("processing", "completed")


class IdempotencyRecord(Base):
    """
    Header `Idempotency-Key` de las operaciones que cobran o gastan puntos. Un reintento con la misma clave
    devuelve la respuesta guardada en vez de cobrar dos veces. La clave es por usuario. `request_hash` detecta
    la misma clave con otro cuerpo (es un error del cliente). `processing` mientras corre: otro pedido con la
    misma clave recibe 409 en vez de ejecutarse en paralelo.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(f"status IN {IDEMPOTENCY_STATUSES}", name="status_valid"),
        CheckConstraint("status <> 'completed' OR response_status IS NOT NULL", name="completed_has_response"),
        # Limpieza de las vencidas
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    user_id: Mapped[str] = mapped_column(String(USER_ID_LENGTH), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # SHA-256 (hex) del método, la ruta y el cuerpo
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
