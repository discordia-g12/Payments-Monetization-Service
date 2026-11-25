"""
Pasarela de pago (ADR-001 §2.5). El servicio cobra a través de `PaymentGateway`; hoy la única implementación es
la simulada. Una real (Mercado Pago, Stripe) es otra clase con los mismos tres métodos.

Nunca se guarda ni se loguea el número de tarjeta: entra a charge() y se descarta.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

ChargeStatus = Literal["approved", "declined", "refunded"]


@dataclass(frozen=True)
class ChargeResult:
    status: ChargeStatus
    # Comprobante del cobro en la pasarela; None si se rechazó
    reference: str | None = None
    # card_declined, insufficient_funds... (solo si se rechazó)
    decline_reason: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class PaymentGatewayError(Exception):
    """La pasarela no respondió o falló: NO se sabe si cobró (lo resuelve la conciliación)."""


class PaymentGateway(Protocol):
    async def charge(self, *, amount_cents: int, currency: str, card_number: str, idempotency_key: str) -> ChargeResult:
        """
        Cobra. `idempotency_key` es el id de la compra: la pasarela no cobra dos veces con la misma clave
        (devuelve el resultado del primer intento).
        """
        ...

    async def refund(self, reference: str) -> None:
        """Devuelve un cobro aprobado (la compensación de la saga). Lanza PaymentGatewayError si no pudo."""
        ...

    async def find_charge(self, idempotency_key: str) -> ChargeResult | None:
        """El cobro hecho con esa clave, o None si nunca se cobró (para conciliar compras a medias)."""
        ...


# Tarjetas de prueba, como las del modo test de Stripe
APPROVED_CARD = "4242424242424242"
DECLINED_CARDS = {
    "4000000000000002": "card_declined",
    "4000000000009995": "insufficient_funds",
}


class SimulatedPaymentGateway:
    """
    Pasarela simulada: aprueba solo la tarjeta 4242 4242 4242 4242 y rechaza todo lo demás (nunca aprueba por
    defecto). Los cobros viven en memoria del proceso, como si fueran los de la pasarela: si el servicio se
    reinicia, find_charge ya no los encuentra y la conciliación da esas compras por no cobradas.
    """

    def __init__(self) -> None:
        self._charges: dict[str, ChargeResult] = {}
        self._lock = asyncio.Lock()

    async def charge(self, *, amount_cents: int, currency: str, card_number: str, idempotency_key: str) -> ChargeResult:
        if amount_cents <= 0:
            raise ValueError("amount_cents tiene que ser positivo")
        digits = "".join(ch for ch in card_number if ch.isdigit())
        async with self._lock:
            if idempotency_key in self._charges:
                return self._charges[idempotency_key]
            if digits == APPROVED_CARD:
                result = ChargeResult("approved", reference=f"sim_{uuid.uuid4().hex}")
            else:
                result = ChargeResult("declined", decline_reason=DECLINED_CARDS.get(digits, "card_declined"))
            self._charges[idempotency_key] = result
            return result

    async def refund(self, reference: str) -> None:
        async with self._lock:
            for key, result in self._charges.items():
                if result.reference == reference:
                    if result.status == "approved":
                        self._charges[key] = ChargeResult("refunded", reference=reference)
                    return
        raise PaymentGatewayError(f"No existe el cobro {reference}")

    async def find_charge(self, idempotency_key: str) -> ChargeResult | None:
        async with self._lock:
            return self._charges.get(idempotency_key)
