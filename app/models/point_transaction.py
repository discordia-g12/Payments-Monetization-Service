import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.wallet import USER_ID_LENGTH

# Tipos de movimiento. purchase: compra de un paquete (crédito). boost: puntos gastados en boostear (débito).
# refund y adjustment quedan para devoluciones y correcciones manuales.
POINT_TRANSACTION_KINDS = ("purchase", "boost", "refund", "adjustment")


class PointTransaction(Base):
    """
    Historial de puntos, solo de agregar: cada fila es un movimiento ya aplicado al saldo. `amount` es positivo
    para créditos y negativo para débitos; `balance_after` es el saldo que quedó, para mostrar el historial sin
    recalcularlo. Una compra rechazada no mueve puntos, así que no queda acá.
    """

    __tablename__ = "point_transactions"
    __table_args__ = (
        CheckConstraint(f"kind IN {POINT_TRANSACTION_KINDS}", name="kind_valid"),
        CheckConstraint("amount <> 0", name="amount_non_zero"),
        CheckConstraint("balance_after >= 0", name="balance_after_non_negative"),
        # Una compra acredita y deja el paquete, el precio y el comprobante del pago
        CheckConstraint(
            "kind <> 'purchase' OR (amount > 0 AND package_id IS NOT NULL AND price_cents IS NOT NULL "
            "AND currency IS NOT NULL AND payment_reference IS NOT NULL)",
            name="purchase_details",
        ),
        # Un boost debita y apunta al boost que pagó
        CheckConstraint("kind <> 'boost' OR (amount < 0 AND server_boost_id IS NOT NULL)", name="boost_details"),
        CheckConstraint("price_cents IS NULL OR price_cents > 0", name="price_positive"),
        # Historial de un usuario, del más nuevo al más viejo
        Index("ix_point_transactions_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(
        String(USER_ID_LENGTH), ForeignKey("wallets.user_id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Compra
    package_id: Mapped[str | None] = mapped_column(String(32))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    # Id del cobro en la pasarela: único, así un mismo cobro no acredita dos veces
    payment_reference: Mapped[str | None] = mapped_column(String(128), unique=True)

    # Boost
    server_boost_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("server_boosts.id", ondelete="RESTRICT")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
