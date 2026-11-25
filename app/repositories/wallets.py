"""Billetera y movimientos (ADR-001 §2.2, §2.3)."""

import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PointTransaction, Wallet


async def lock_wallet(session: AsyncSession, user_id: str) -> Wallet:
    """
    La billetera del usuario, bloqueada hasta el fin de la transacción (`SELECT ... FOR UPDATE`). Si no tenía,
    la crea en 0. Todo lo que mueve puntos empieza por acá; si además bloquea el contador de un servidor, lo hace
    después (orden fijo, ADR-001 §2.3).
    """
    # ON CONFLICT DO NOTHING: dos primeras compras simultáneas no chocan al crear la billetera
    await session.execute(insert(Wallet).values(user_id=user_id, balance=0).on_conflict_do_nothing())
    result = await session.execute(
        select(Wallet).where(Wallet.user_id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def get_balance(session: AsyncSession, user_id: str) -> int:
    """Saldo sin bloquear (para mostrar). Sin billetera, 0."""
    balance = await session.scalar(select(Wallet.balance).where(Wallet.user_id == user_id))
    return balance or 0


def apply_movement(session: AsyncSession, wallet: Wallet, amount: int, kind: str, **details) -> PointTransaction:
    """
    Suma `amount` (negativo para débitos) al saldo de una billetera ya bloqueada y anota el movimiento. El que
    llama valida que alcance; el CHECK de la base es la última red.
    """
    wallet.balance += amount
    movement = PointTransaction(
        user_id=wallet.user_id, kind=kind, amount=amount, balance_after=wallet.balance, **details
    )
    session.add(movement)
    return movement


async def list_movements(
    session: AsyncSession, user_id: str, limit: int, before: uuid.UUID | None = None
) -> tuple[list[PointTransaction], uuid.UUID | None]:
    """
    Movimientos del más nuevo al más viejo, de a `limit`. `before` es el id del último de la página anterior;
    devuelve el cursor de la siguiente (None si no hay más).
    """
    query = select(PointTransaction).where(PointTransaction.user_id == user_id)
    if before is not None:
        anchor = (
            await session.execute(
                select(PointTransaction.created_at, PointTransaction.id).where(
                    PointTransaction.id == before, PointTransaction.user_id == user_id
                )
            )
        ).first()
        if anchor is None:
            return [], None
        query = query.where(tuple_(PointTransaction.created_at, PointTransaction.id) < tuple_(*anchor))
    rows = list(
        (
            await session.execute(
                query.order_by(PointTransaction.created_at.desc(), PointTransaction.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, (page[-1].id if has_more else None)
