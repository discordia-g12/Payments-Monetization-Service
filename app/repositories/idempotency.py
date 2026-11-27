"""
Idempotency-Key para lo que gasta puntos y no es una compra (ADR-001 §2.4). La compra usa su propia fila
(point_purchases); el boost usa idempotency_records.

Uso, dentro de la transacción de la operación:

    claim = await claim_key(session, user_id, key, request_hash)
    if claim.replay is not None:
        return claim.replay           # ya se hizo: la misma respuesta
    ... hacer la operación ...
    complete(claim, status_code, body)
    await session.commit()            # la clave y la operación se guardan juntas, o ninguna

La clave se reclama con un INSERT en la misma transacción: un segundo pedido con la misma clave espera ahí a que
el primero termine. Si el primero confirma, el segundo recibe su respuesta; si falla (saldo insuficiente, no es
miembro), la transacción se deshace con la clave incluida y el reintento vuelve a intentar. Solo se guardan las
respuestas exitosas.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import IdempotencyRecord

TTL = timedelta(hours=24)


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: Any


@dataclass
class Claim:
    record: IdempotencyRecord | None
    replay: StoredResponse | None = None


def request_hash(method: str, path: str, body: str = "") -> str:
    return hashlib.sha256(f"{method} {path}\n{body}".encode()).hexdigest()


async def claim_key(session: AsyncSession, user_id: str, key: str, hashed: str) -> Claim:
    now = datetime.now(UTC)
    # Una vencida no cuenta: se borra y la clave se puede volver a usar
    await session.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key, IdempotencyRecord.expires_at <= now
        )
    )
    claimed = await session.scalar(
        insert(IdempotencyRecord)
        .values(user_id=user_id, key=key, request_hash=hashed, status="processing", expires_at=now + TTL)
        .on_conflict_do_nothing()
        .returning(IdempotencyRecord.key)
    )
    if claimed is not None:
        record = await session.get(IdempotencyRecord, (user_id, key))
        return Claim(record)

    existing = await session.get(IdempotencyRecord, (user_id, key), populate_existing=True)
    if existing.request_hash != hashed:
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "idempotency_key_reused",
            "Esa Idempotency-Key ya se usó para otro pedido.",
        )
    if existing.status != "completed":
        # No pasa con una sola transacción por operación, pero por las dudas no se ejecuta dos veces
        raise AppError(status.HTTP_409_CONFLICT, "request_in_progress", "Ese pedido todavía se está procesando.")
    return Claim(None, StoredResponse(existing.response_status, existing.response_body))


def complete(claim: Claim, status_code: int, body: Any) -> None:
    """Guarda la respuesta exitosa (se confirma con la operación)."""
    claim.record.status = "completed"
    claim.record.response_status = status_code
    claim.record.response_body = body
