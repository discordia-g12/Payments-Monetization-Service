import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BoostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    server_id: str
    points_spent: int
    created_at: datetime
    removed_at: datetime | None


class ServerBoostStatusOut(BaseModel):
    server_id: str
    active_boosts: int
    level: int


class BoostResultOut(BaseModel):
    boost: BoostOut
    server: ServerBoostStatusOut
    # Saldo después de pagar el boost
    balance: int


class RemoveBoostResultOut(BaseModel):
    boost: BoostOut
    server: ServerBoostStatusOut


class MyBoostsOut(BaseModel):
    boosts: list[BoostOut]
