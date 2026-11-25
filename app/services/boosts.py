"""
Boostear un servidor y retirar un boost (ADR-001 §2.3, §2.7, §2.9). A diferencia de la compra, todo pasa en
UNA transacción local (no hay nada externo que compensar): la membresía se consulta antes de tocar nada.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.server_roles import MembershipUnavailableError, ServerRolesClient
from app.core.errors import AppError
from app.models import ServerBoost, ServerBoostCounter
from app.repositories import boosts as boosts_repo
from app.repositories import idempotency, outbox, wallets
from app.repositories.idempotency import StoredResponse
from app.schemas.boosts import BoostOut, BoostResultOut, RemoveBoostResultOut, ServerBoostStatusOut
from app.services.boost_levels import BOOST_COST_POINTS, level_for


def _server_status(counter: ServerBoostCounter) -> ServerBoostStatusOut:
    return ServerBoostStatusOut(server_id=counter.server_id, active_boosts=counter.active_boosts, level=counter.level)


def _apply_count(session: AsyncSession, counter: ServerBoostCounter, delta: int, user_id: str) -> None:
    """Suma o resta un boost vigente, recalcula el nivel y, si cambió, deja server.boost_level_changed."""
    previous_level = counter.level
    counter.active_boosts += delta
    counter.level = level_for(counter.active_boosts)
    if counter.level != previous_level:
        outbox.add_event(
            session,
            "server.boost_level_changed",
            "server",
            counter.server_id,
            {
                "server_id": counter.server_id,
                "previous_level": previous_level,
                "level": counter.level,
                "active_boosts": counter.active_boosts,
                # Quién lo provocó (el boost que cruzó el umbral, o el retiro que lo bajó)
                "user_id": user_id,
            },
        )


async def _check_membership(server_roles: ServerRolesClient, server_id: str, user_id: str) -> None:
    try:
        members = await server_roles.member_ids(server_id)
    except MembershipUnavailableError:
        # Nunca se boostea sin verificar
        raise AppError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "membership_unavailable",
            "No pudimos verificar que seas miembro del servidor. Probá de nuevo en un rato.",
        ) from None
    if members is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "server_not_found", "El servidor no existe.")
    if user_id not in members:
        raise AppError(status.HTTP_403_FORBIDDEN, "not_a_member", "Solo los miembros pueden boostear el servidor.")


async def boost_server(
    session: AsyncSession, server_roles: ServerRolesClient, *, user_id: str, server_id: str, idempotency_key: str
) -> StoredResponse:
    """Gasta BOOST_COST_POINTS en un boost para el servidor (CA 1 a CA 3). Devuelve la respuesta a enviar."""
    hashed = idempotency.request_hash("POST", f"/api/v1/boosts/servers/{server_id}")
    async with session.begin():
        claim = await idempotency.claim_key(session, user_id, idempotency_key, hashed)
        if claim.replay is not None:
            return claim.replay

        await _check_membership(server_roles, server_id, user_id)

        # Orden de bloqueo: billetera y después contador del servidor
        wallet = await wallets.lock_wallet(session, user_id)
        if wallet.balance < BOOST_COST_POINTS:
            raise AppError(
                status.HTTP_409_CONFLICT,
                "insufficient_points",
                f"Te faltan {BOOST_COST_POINTS - wallet.balance} puntos para boostear este servidor.",
                balance=wallet.balance,
                cost=BOOST_COST_POINTS,
                missing=BOOST_COST_POINTS - wallet.balance,
            )
        counter = await boosts_repo.lock_counter(session, server_id)

        now = datetime.now(UTC)
        boost = ServerBoost(
            id=uuid.uuid4(), server_id=server_id, user_id=user_id, points_spent=BOOST_COST_POINTS, created_at=now
        )
        session.add(boost)
        await session.flush()  # el movimiento apunta al boost (clave foránea)
        movement = wallets.apply_movement(session, wallet, -BOOST_COST_POINTS, "boost", server_boost_id=boost.id)
        _apply_count(session, counter, +1, user_id)
        outbox.add_event(
            session,
            "server.boosted",
            "server",
            server_id,
            {
                "boost_id": str(boost.id),
                "server_id": server_id,
                "user_id": user_id,
                "points_spent": BOOST_COST_POINTS,
                "active_boosts": counter.active_boosts,
                "level": counter.level,
                "transaction_id": str(movement.id),
            },
        )

        body: dict[str, Any] = BoostResultOut(
            boost=BoostOut.model_validate(boost), server=_server_status(counter), balance=wallet.balance
        ).model_dump(mode="json")
        idempotency.complete(claim, status.HTTP_201_CREATED, body)
    return StoredResponse(status.HTTP_201_CREATED, body)


async def remove_boost(session: AsyncSession, *, user_id: str, boost_id: uuid.UUID) -> dict[str, Any]:
    """Retira un boost propio, sin reembolso (CA 4). El servidor pierde el nivel si queda debajo del umbral."""
    async with session.begin():
        boost = await boosts_repo.lock_own_boost(session, boost_id, user_id)
        if boost is None:
            raise AppError(status.HTTP_404_NOT_FOUND, "boost_not_found", "No tenés un boost con ese id.")
        if boost.removed_at is not None:
            raise AppError(status.HTTP_409_CONFLICT, "boost_already_removed", "Ese boost ya lo retiraste.")

        # Orden de bloqueo: el boost (no lo bloquea nadie que boostee) y después el contador
        counter = await boosts_repo.lock_counter(session, boost.server_id)
        boost.removed_at = datetime.now(UTC)
        _apply_count(session, counter, -1, user_id)
        outbox.add_event(
            session,
            "server.boost_removed",
            "server",
            boost.server_id,
            {
                "boost_id": str(boost.id),
                "server_id": boost.server_id,
                "user_id": user_id,
                "active_boosts": counter.active_boosts,
                "level": counter.level,
            },
        )
        return RemoveBoostResultOut(boost=BoostOut.model_validate(boost), server=_server_status(counter)).model_dump(
            mode="json"
        )


async def my_boosts(session: AsyncSession, user_id: str, server_id: str | None = None) -> list[ServerBoost]:
    return await boosts_repo.list_active(session, user_id, server_id)
