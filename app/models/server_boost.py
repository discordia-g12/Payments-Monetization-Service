import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.wallet import USER_ID_LENGTH

# Id de servidor de Server-Roles (UUID en texto)
SERVER_ID_LENGTH = 64
MAX_BOOST_LEVEL = 3


class ServerBoost(Base):
    """
    Un boost: una unidad independiente que un miembro le da a un servidor, con su propio costo. Un miembro puede
    tener varios en el mismo servidor. Retirarlo pone `removed_at` (sin reembolso): la fila queda como
    historial y el pago (point_transactions) la sigue apuntando.
    """

    __tablename__ = "server_boosts"
    __table_args__ = (
        CheckConstraint("points_spent > 0", name="points_spent_positive"),
        CheckConstraint("removed_at IS NULL OR removed_at >= created_at", name="removed_after_created"),
        # Boosts vigentes de un servidor (contarlos, listarlos)
        Index("ix_server_boosts_active_server_id", "server_id", postgresql_where=text("removed_at IS NULL")),
        # Los boosts de un miembro en un servidor (para retirar uno propio)
        Index("ix_server_boosts_user_id_server_id", "user_id", "server_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[str] = mapped_column(String(SERVER_ID_LENGTH), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(USER_ID_LENGTH), ForeignKey("wallets.user_id", ondelete="RESTRICT"), nullable=False
    )
    points_spent: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServerBoostCounter(Base):
    """
    Boosts vigentes y nivel de cada servidor. Es la fila que se bloquea (`SELECT ... FOR UPDATE`) al boostear o
    retirar: así dos boosts a la vez del mismo servidor se ordenan y el que cruza un umbral (5, 15, 30) se
    detecta una sola vez. También evita contar server_boosts en cada consulta del nivel.
    """

    __tablename__ = "server_boost_counters"
    __table_args__ = (
        CheckConstraint("active_boosts >= 0", name="active_boosts_non_negative"),
        CheckConstraint(f"level BETWEEN 0 AND {MAX_BOOST_LEVEL}", name="level_valid"),
    )

    server_id: Mapped[str] = mapped_column(String(SERVER_ID_LENGTH), primary_key=True)
    active_boosts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
