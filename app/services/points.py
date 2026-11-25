"""
Compra de puntos: una saga chica dentro de Payments (ADR-001 §2.11).

    1. Registrar la compra en `pending` y confirmar.
    2. Cobrar en la pasarela, con el id de la compra como clave de idempotencia.
    3. En UNA transacción: acreditar en la billetera, anotar el movimiento, marcar la compra `completed` y dejar
       los eventos en el outbox.

El cobro (paso 2) es externo: no entra en la transacción de la base. Por eso:
    - Si el paso 3 falla, se compensa: se devuelve el cobro y la compra queda `refunded`.
    - Si se corta a mitad (cae el proceso, la pasarela no responde, la devolución falla), la compra queda
      `pending` y la conciliación (reconcile_pending_purchases) le pregunta a la pasarela qué pasó con ese cobro
      y la termina: la acredita, la da por fallida o la marca devuelta.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.models import PointPurchase, PointTransaction
from app.repositories import outbox, wallets
from app.services.catalog import PointPackage, get_package
from app.services.payment_gateway import PaymentGateway, PaymentGatewayError

logger = logging.getLogger("payments.points")

SessionFactory = async_sessionmaker[AsyncSession]

# Una compra pendiente más vieja que esto se concilia: ningún cobro tarda tanto (la pasarela corta antes)
RECONCILE_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class PurchaseOutcome:
    purchase: PointPurchase
    balance: int


# ── Errores que ve el cliente ──


def _unknown_package(package_id: str) -> AppError:
    return AppError(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_package", f"No existe el paquete {package_id}.")


def _declined(purchase: PointPurchase) -> AppError:
    return AppError(
        status.HTTP_402_PAYMENT_REQUIRED,
        "payment_declined",
        "El pago fue rechazado: no se acreditaron puntos.",
        reason=purchase.failure_reason,
        purchase_id=str(purchase.id),
    )


def _pending(purchase_id: uuid.UUID) -> AppError:
    return AppError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "payment_pending",
        "No pudimos confirmar el pago. Si se cobró, los puntos se acreditan en unos minutos; si no, no se cobra nada.",
        purchase_id=str(purchase_id),
    )


def _refunded(purchase_id: uuid.UUID) -> AppError:
    return AppError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "purchase_refunded",
        "No pudimos acreditar los puntos, así que devolvimos el pago. Probá de nuevo en un rato.",
        purchase_id=str(purchase_id),
    )


# ── Pasos de la saga ──


async def _lock_purchase(session: AsyncSession, purchase_id: uuid.UUID) -> PointPurchase:
    result = await session.execute(
        select(PointPurchase)
        .where(PointPurchase.id == purchase_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _start(
    sessions: SessionFactory, user_id: str, idempotency_key: str, package: PointPackage
) -> tuple[PointPurchase, bool]:
    """Paso 1. Devuelve la compra y si es nueva (False: ya había una con esa Idempotency-Key)."""
    async with sessions() as session:
        purchase = PointPurchase(
            user_id=user_id,
            idempotency_key=idempotency_key,
            package_id=package.id,
            points=package.points,
            price_cents=package.price_cents,
            currency=package.currency,
            status="pending",
        )
        session.add(purchase)
        try:
            await session.commit()
            return purchase, True
        except IntegrityError:
            # La misma clave ya se usó (un reintento, o dos pedidos iguales a la vez)
            await session.rollback()
        existing = await session.scalar(
            select(PointPurchase).where(
                PointPurchase.user_id == user_id, PointPurchase.idempotency_key == idempotency_key
            )
        )
        if existing is None:
            # La IntegrityError no era por la clave repetida: es un error de verdad
            raise RuntimeError("No se pudo registrar la compra")
        return existing, False


async def _complete(sessions: SessionFactory, purchase_id: uuid.UUID, reference: str) -> PurchaseOutcome:
    """Paso 3: acredita. Si la compra ya no está pendiente (la terminó la conciliación), no hace nada."""
    async with sessions() as session:
        purchase = await _lock_purchase(session, purchase_id)
        if purchase.status != "pending":
            return PurchaseOutcome(purchase, await wallets.get_balance(session, purchase.user_id))

        # Orden de bloqueo: la compra (fila propia de esta operación) y después la billetera
        wallet = await wallets.lock_wallet(session, purchase.user_id)
        movement = wallets.apply_movement(
            session,
            wallet,
            purchase.points,
            "purchase",
            package_id=purchase.package_id,
            price_cents=purchase.price_cents,
            currency=purchase.currency,
            payment_reference=reference,
        )
        purchase.status = "completed"
        purchase.payment_reference = reference
        purchase.completed_at = datetime.now(UTC)
        await session.flush()

        common = {
            "purchase_id": str(purchase.id),
            "user_id": purchase.user_id,
            "amount_cents": purchase.price_cents,
            "currency": purchase.currency,
        }
        outbox.add_event(
            session,
            "payment.completed",
            "point_purchase",
            str(purchase.id),
            {**common, "payment_reference": reference},
        )
        outbox.add_event(
            session,
            "points.purchased",
            "wallet",
            purchase.user_id,
            {
                **common,
                "package_id": purchase.package_id,
                "points": purchase.points,
                "balance": wallet.balance,
                "transaction_id": str(movement.id),
            },
        )
        await session.commit()
        return PurchaseOutcome(purchase, wallet.balance)


async def _fail(sessions: SessionFactory, purchase_id: uuid.UUID, reason: str) -> PointPurchase:
    """El cobro se rechazó (o nunca se hizo): la compra queda `failed`, sin mover puntos."""
    async with sessions() as session:
        purchase = await _lock_purchase(session, purchase_id)
        if purchase.status == "pending":
            purchase.status = "failed"
            purchase.failure_reason = reason
            outbox.add_event(
                session,
                "payment.failed",
                "point_purchase",
                str(purchase.id),
                {
                    "purchase_id": str(purchase.id),
                    "user_id": purchase.user_id,
                    "package_id": purchase.package_id,
                    "amount_cents": purchase.price_cents,
                    "currency": purchase.currency,
                    "reason": reason,
                },
            )
            await session.commit()
        return purchase


async def _mark_refunded(sessions: SessionFactory, purchase_id: uuid.UUID, reference: str) -> PointPurchase:
    async with sessions() as session:
        purchase = await _lock_purchase(session, purchase_id)
        if purchase.status == "pending":
            purchase.status = "refunded"
            purchase.payment_reference = reference
            outbox.add_event(
                session,
                "payment.refunded",
                "point_purchase",
                str(purchase.id),
                {
                    "purchase_id": str(purchase.id),
                    "user_id": purchase.user_id,
                    "amount_cents": purchase.price_cents,
                    "currency": purchase.currency,
                    "payment_reference": reference,
                },
            )
            await session.commit()
        return purchase


async def _compensate(sessions: SessionFactory, gateway: PaymentGateway, purchase_id: uuid.UUID, reference: str):
    """
    Compensación: el cobro salió pero no se pudo acreditar, así que se devuelve. Si la devolución también falla,
    la compra queda pendiente y la conciliación la acredita (el cobro sigue aprobado en la pasarela).
    """
    try:
        await gateway.refund(reference)
    except PaymentGatewayError:
        logger.exception(
            "No se pudo devolver el cobro %s de la compra %s: queda para conciliar", reference, purchase_id
        )
        raise _pending(purchase_id) from None
    try:
        await _mark_refunded(sessions, purchase_id, reference)
    except Exception:
        # La devolución ya se hizo: la conciliación la ve en la pasarela y marca la compra
        logger.exception("Cobro %s devuelto pero la compra %s no se pudo marcar", reference, purchase_id)
    raise _refunded(purchase_id)


async def _replay(sessions: SessionFactory, purchase: PointPurchase, package_id: str) -> PurchaseOutcome:
    """Reintento con la misma Idempotency-Key: la misma respuesta que la primera vez, sin cobrar de nuevo."""
    if purchase.package_id != package_id:
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "idempotency_key_reused",
            "Esa Idempotency-Key ya se usó para otra compra.",
        )
    if purchase.status == "completed":
        async with sessions() as session:
            return PurchaseOutcome(purchase, await wallets.get_balance(session, purchase.user_id))
    if purchase.status == "failed":
        raise _declined(purchase)
    if purchase.status == "refunded":
        raise _refunded(purchase.id)
    raise AppError(
        status.HTTP_409_CONFLICT,
        "purchase_in_progress",
        "Esta compra todavía se está procesando.",
        purchase_id=str(purchase.id),
    )


# ── API del servicio ──


async def purchase_points(
    sessions: SessionFactory,
    gateway: PaymentGateway,
    *,
    user_id: str,
    idempotency_key: str,
    package_id: str,
    card_number: str,
) -> PurchaseOutcome:
    """Compra un paquete (CA 1 y CA 2). Lanza AppError con lo que ve el cliente si no se acreditó."""
    package = get_package(package_id)
    if package is None:
        raise _unknown_package(package_id)

    purchase, created = await _start(sessions, user_id, idempotency_key, package)
    if not created:
        return await _replay(sessions, purchase, package_id)

    try:
        charge = await gateway.charge(
            amount_cents=package.price_cents,
            currency=package.currency,
            card_number=card_number,
            idempotency_key=str(purchase.id),
        )
    except PaymentGatewayError:
        # No se sabe si cobró: queda pendiente y la conciliación lo resuelve
        logger.warning("La pasarela no respondió al cobrar la compra %s", purchase.id)
        raise _pending(purchase.id) from None

    if not charge.approved:
        raise _declined(await _fail(sessions, purchase.id, charge.decline_reason or "card_declined"))

    try:
        return await _complete(sessions, purchase.id, charge.reference)
    except Exception:
        logger.exception("No se pudo acreditar la compra %s: se devuelve el cobro", purchase.id)
        await _compensate(sessions, gateway, purchase.id, charge.reference)
        raise  # _compensate siempre lanza; esto no se alcanza


async def reconcile_pending_purchases(
    sessions: SessionFactory,
    gateway: PaymentGateway,
    *,
    older_than: timedelta = RECONCILE_AFTER,
    now: datetime | None = None,
    limit: int = 50,
) -> int:
    """
    Termina las compras que quedaron pendientes: le pregunta a la pasarela por el cobro de cada una (con su id).
    Aprobado → la acredita. Rechazado o inexistente → fallida. Devuelto → devuelta. Devuelve cuántas cerró.
    """
    cutoff = (now or datetime.now(UTC)) - older_than
    async with sessions() as session:
        stale = list(
            (
                await session.execute(
                    select(PointPurchase.id)
                    .where(PointPurchase.status == "pending", PointPurchase.updated_at < cutoff)
                    .order_by(PointPurchase.updated_at)
                    .limit(limit)
                )
            ).scalars()
        )

    closed = 0
    for purchase_id in stale:
        try:
            charge = await gateway.find_charge(str(purchase_id))
            if charge is None:
                await _fail(sessions, purchase_id, "not_charged")
            elif charge.status == "approved":
                await _complete(sessions, purchase_id, charge.reference)
            elif charge.status == "refunded":
                await _mark_refunded(sessions, purchase_id, charge.reference)
            else:
                await _fail(sessions, purchase_id, charge.decline_reason or "card_declined")
            closed += 1
        except Exception:
            # Una que falla no frena a las demás: se reintenta en la próxima vuelta
            logger.exception("No se pudo conciliar la compra %s", purchase_id)
    return closed


# ── Saldo e historial (CA 3) ──


@dataclass(frozen=True)
class WalletView:
    balance: int
    movements: list[PointTransaction]
    next_cursor: uuid.UUID | None


async def get_wallet(
    session: AsyncSession, user_id: str, limit: int = 20, before: uuid.UUID | None = None
) -> WalletView:
    balance = await wallets.get_balance(session, user_id)
    movements, next_cursor = await wallets.list_movements(session, user_id, limit, before)
    return WalletView(balance, movements, next_cursor)
