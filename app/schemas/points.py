import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PackageOut(BaseModel):
    id: str
    points: int
    price_cents: int
    currency: str


class PackagesOut(BaseModel):
    packages: list[PackageOut]


class PurchaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(max_length=32, examples=["points_500"])
    # Solo tarjetas de prueba (ADR-001 §2.5). repr=False: nunca aparece en logs ni en errores
    card_number: str = Field(
        min_length=12, max_length=23, pattern=r"^[0-9 ]+$", repr=False, examples=["4242 4242 4242 4242"]
    )


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    package_id: str
    points: int
    price_cents: int
    currency: str
    payment_reference: str | None
    created_at: datetime
    completed_at: datetime | None


class PurchaseResultOut(BaseModel):
    purchase: PurchaseOut
    # Saldo después de la compra
    balance: int


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount: int
    balance_after: int
    package_id: str | None
    price_cents: int | None
    currency: str | None
    server_boost_id: uuid.UUID | None
    created_at: datetime


class WalletOut(BaseModel):
    balance: int
    movements: list[MovementOut]
    # Para la página siguiente: ?before=<next_cursor>. None si no hay más
    next_cursor: uuid.UUID | None
