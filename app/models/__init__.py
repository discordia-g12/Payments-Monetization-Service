# Importar todos los modelos acá los registra en Base.metadata (lo usan Alembic y los tests)
from app.models.idempotency import IdempotencyRecord
from app.models.outbox import OutboxEvent
from app.models.point_purchase import PointPurchase
from app.models.point_transaction import PointTransaction
from app.models.server_boost import ServerBoost, ServerBoostCounter
from app.models.subscription import Subscription
from app.models.wallet import Wallet

__all__ = [
    "IdempotencyRecord",
    "OutboxEvent",
    "PointPurchase",
    "PointTransaction",
    "ServerBoost",
    "ServerBoostCounter",
    "Subscription",
    "Wallet",
]
