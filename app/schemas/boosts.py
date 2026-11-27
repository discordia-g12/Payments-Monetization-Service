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


class BenefitOut(BaseModel):
    id: str
    name: str
    description: str
    # El nivel que lo desbloquea
    level: int


class LevelOut(BaseModel):
    level: int
    required_boosts: int
    unlocked: bool
    benefits: list[BenefitOut]


class ServerBoostInfoOut(BaseModel):
    """Nivel, beneficios y progreso de un servidor (historia Ver beneficios de servidor boosteado)."""

    server_id: str
    active_boosts: int
    level: int
    # El próximo nivel y cuántos boosts faltan. En el máximo: null y 0
    next_level: int | None
    boosts_to_next_level: int
    # Los boosts vigentes de quien consulta (los que puede retirar)
    my_active_boosts: int
    boost_cost_points: int
    unlocked_benefits: list[BenefitOut]
    levels: list[LevelOut]
