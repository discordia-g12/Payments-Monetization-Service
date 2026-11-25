import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, Gateway, IdempotencyKey, Sessions
from app.db.session import get_db
from app.schemas.points import (
    MovementOut,
    PackageOut,
    PackagesOut,
    PurchaseIn,
    PurchaseOut,
    PurchaseResultOut,
    WalletOut,
)
from app.services import points
from app.services.catalog import POINT_PACKAGES

router = APIRouter(prefix="/api/v1/points", tags=["Points"])

ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Sin X-User-Id (el pedido no pasó por Kong)"},
}


@router.get("/packages", response_model=PackagesOut, summary="Paquetes de puntos a la venta")
async def list_packages(_: CurrentUser) -> PackagesOut:
    return PackagesOut(packages=[PackageOut(**vars(package)) for package in POINT_PACKAGES.values()])


@router.post(
    "/purchases",
    response_model=PurchaseResultOut,
    status_code=status.HTTP_201_CREATED,
    summary="Comprar un paquete de puntos",
    description=(
        "Cobra el paquete y acredita los puntos (ADR-001 §2.11). Exige `Idempotency-Key`: un reintento con la "
        "misma clave devuelve el mismo resultado sin cobrar de nuevo. Tarjetas de prueba: 4242 4242 4242 4242 "
        "(aprobada), 4000 0000 0000 0002 y 4000 0000 0000 9995 (rechazadas)."
    ),
    responses={
        **ERRORS,
        status.HTTP_400_BAD_REQUEST: {"description": "Falta Idempotency-Key"},
        status.HTTP_402_PAYMENT_REQUIRED: {"description": "Pago rechazado: no se acreditó nada (`reason`)"},
        status.HTTP_409_CONFLICT: {"description": "Esa compra todavía se está procesando"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Paquete inexistente, o clave reusada con otro"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "No se pudo confirmar (`payment_pending`) o se devolvió el pago (`purchase_refunded`)"
        },
    },
)
async def purchase(
    body: PurchaseIn, user_id: CurrentUser, idempotency_key: IdempotencyKey, sessions: Sessions, gateway: Gateway
) -> PurchaseResultOut:
    outcome = await points.purchase_points(
        sessions,
        gateway,
        user_id=user_id,
        idempotency_key=idempotency_key,
        package_id=body.package_id,
        card_number=body.card_number,
    )
    return PurchaseResultOut(purchase=PurchaseOut.model_validate(outcome.purchase), balance=outcome.balance)


@router.get(
    "/wallet",
    response_model=WalletOut,
    summary="Saldo e historial de puntos",
    description="Saldo y movimientos (compras, boosts), del más nuevo al más viejo. Sin compras: saldo 0.",
    responses=ERRORS,
)
async def wallet(
    user_id: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[uuid.UUID | None, Query(description="next_cursor de la página anterior")] = None,
) -> WalletOut:
    view = await points.get_wallet(session, user_id, limit, before)
    return WalletOut(
        balance=view.balance,
        movements=[MovementOut.model_validate(movement) for movement in view.movements],
        next_cursor=view.next_cursor,
    )
