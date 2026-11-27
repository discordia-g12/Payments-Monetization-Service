"""Boosts y contador por servidor (ADR-001 §2.3, §2.7)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServerBoost, ServerBoostCounter


async def lock_counter(session: AsyncSession, server_id: str) -> ServerBoostCounter:
    """
    El contador del servidor, bloqueado (`SELECT ... FOR UPDATE`); lo crea en 0 si no existía. Se bloquea
    DESPUÉS de la billetera (orden fijo, ADR-001 §2.3): así dos boosts simultáneos del mismo servidor se
    ordenan y el que cruza un umbral se detecta una sola vez.
    """
    await session.execute(
        insert(ServerBoostCounter).values(server_id=server_id, active_boosts=0, level=0).on_conflict_do_nothing()
    )
    result = await session.execute(
        select(ServerBoostCounter)
        .where(ServerBoostCounter.server_id == server_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def get_counter(session: AsyncSession, server_id: str) -> ServerBoostCounter | None:
    """Sin bloquear (para mostrar). None si el servidor nunca tuvo boosts."""
    return await session.get(ServerBoostCounter, server_id)


async def lock_own_boost(session: AsyncSession, boost_id: uuid.UUID, user_id: str) -> ServerBoost | None:
    """El boost, bloqueado, solo si es de ese usuario (el de otro es como si no existiera)."""
    result = await session.execute(
        select(ServerBoost)
        .where(ServerBoost.id == boost_id, ServerBoost.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_active(session: AsyncSession, user_id: str, server_id: str | None = None) -> list[ServerBoost]:
    """Los boosts vigentes de un usuario (en un servidor, o en todos), del más nuevo al más viejo."""
    query = select(ServerBoost).where(ServerBoost.user_id == user_id, ServerBoost.removed_at.is_(None))
    if server_id is not None:
        query = query.where(ServerBoost.server_id == server_id)
    result = await session.execute(query.order_by(ServerBoost.created_at.desc(), ServerBoost.id.desc()))
    return list(result.scalars())


async def count_active(session: AsyncSession, user_id: str, server_id: str) -> int:
    """Cuántos boosts vigentes dio un usuario a un servidor."""
    return await session.scalar(
        select(func.count())
        .select_from(ServerBoost)
        .where(ServerBoost.user_id == user_id, ServerBoost.server_id == server_id, ServerBoost.removed_at.is_(None))
    )
