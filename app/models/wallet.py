from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Id canónico del usuario: el que pone Kong en X-User-Id (el `sub` de Auth0 resuelto por Auth-User)
USER_ID_LENGTH = 128


class Wallet(Base):
    """
    Saldo de puntos de un usuario. Toda operación que lo cambia lo bloquea primero
    (`SELECT ... FOR UPDATE`) y anota el movimiento en point_transactions en la misma transacción: el saldo es
    siempre la suma del historial, y dos compras o boosts simultáneos no se pisan.
    """

    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("balance >= 0", name="balance_non_negative"),)

    user_id: Mapped[str] = mapped_column(String(USER_ID_LENGTH), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
