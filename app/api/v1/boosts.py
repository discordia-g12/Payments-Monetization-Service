import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, IdempotencyKey, ServerRoles
from app.db.session import get_db
from app.schemas.boosts import BoostOut, BoostResultOut, MyBoostsOut, RemoveBoostResultOut, ServerBoostInfoOut
from app.services import boosts
from app.services.boost_levels import BOOST_COST_POINTS

router = APIRouter(prefix="/api/v1/boosts", tags=["Boosts"])

Session = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/servers/{server_id}",
    response_model=BoostResultOut,
    status_code=status.HTTP_201_CREATED,
    summary="Boostear un servidor",
    description=(
        f"Gasta {BOOST_COST_POINTS} puntos en un boost para el servidor (ADR-001 §2.7). Cada boost es una unidad "
        "independiente y permanente: un miembro puede dar varios. Exige `Idempotency-Key`: un reintento con la "
        "misma clave devuelve la misma respuesta sin gastar de nuevo."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Falta Idempotency-Key"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Sin X-User-Id"},
        status.HTTP_403_FORBIDDEN: {"description": "No es miembro del servidor (`not_a_member`)"},
        status.HTTP_404_NOT_FOUND: {"description": "El servidor no existe"},
        status.HTTP_409_CONFLICT: {"description": "Saldo insuficiente: `missing` dice cuántos puntos faltan"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "No se pudo verificar la membresía"},
    },
)
async def boost_server(
    server_id: uuid.UUID,
    user_id: CurrentUser,
    idempotency_key: IdempotencyKey,
    session: Session,
    server_roles: ServerRoles,
) -> JSONResponse:
    stored = await boosts.boost_server(
        session, server_roles, user_id=user_id, server_id=str(server_id), idempotency_key=idempotency_key
    )
    return JSONResponse(status_code=stored.status_code, content=stored.body)


@router.get(
    "/servers/{server_id}",
    response_model=ServerBoostInfoOut,
    summary="Nivel, beneficios y progreso de un servidor",
    description=(
        "El nivel de boost del servidor, los beneficios que tiene desbloqueados, todos los niveles con lo que da cada "
        "uno, y cuántos boosts faltan para el siguiente. Solo para miembros. Sin boosts: nivel 0."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Sin X-User-Id"},
        status.HTTP_403_FORBIDDEN: {"description": "No es miembro del servidor (`not_a_member`)"},
        status.HTTP_404_NOT_FOUND: {"description": "El servidor no existe"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "No se pudo verificar la membresía"},
    },
)
async def server_boost_info(
    server_id: uuid.UUID, user_id: CurrentUser, session: Session, server_roles: ServerRoles
) -> ServerBoostInfoOut:
    return await boosts.server_boost_info(session, server_roles, user_id=user_id, server_id=str(server_id))


@router.delete(
    "/{boost_id}",
    response_model=RemoveBoostResultOut,
    summary="Retirar un boost propio",
    description="Sin reembolso de los puntos. El servidor pierde el nivel si queda debajo del umbral.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Sin X-User-Id"},
        status.HTTP_404_NOT_FOUND: {"description": "No existe, o es de otro usuario"},
        status.HTTP_409_CONFLICT: {"description": "Ya estaba retirado"},
    },
)
async def remove_boost(boost_id: uuid.UUID, user_id: CurrentUser, session: Session) -> JSONResponse:
    return JSONResponse(content=await boosts.remove_boost(session, user_id=user_id, boost_id=boost_id))


@router.get("/mine", response_model=MyBoostsOut, summary="Mis boosts vigentes")
async def my_boosts(
    user_id: CurrentUser,
    session: Session,
    server_id: Annotated[uuid.UUID | None, Query(description="Solo los de este servidor")] = None,
) -> MyBoostsOut:
    found = await boosts.my_boosts(session, user_id, str(server_id) if server_id else None)
    return MyBoostsOut(boosts=[BoostOut.model_validate(boost) for boost in found])
