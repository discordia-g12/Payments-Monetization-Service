from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Configuración del servicio. Sale de variables de entorno (no lee .env)."""

    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "development"
    service_name: str = "payments-service"

    # PostgreSQL en formato de SQLAlchemy. Se acepta cualquier driver (el compose viejo dice psycopg2): el
    # servicio siempre usa asyncpg (async_database_url). Sin ella, /readyz responde 503.
    database_url: str | None = None

    # Server-Roles, para verificar la membresía al boostear (ADR-001 §2.9). Sin ella, boostear responde 503.
    # En producción: http://<EDGE_HOST>:8002 (Payments corre en infra, Server-Roles en edge).
    server_roles_url: str | None = None
    server_roles_timeout_seconds: float = 2.0

    # RabbitMQ, para publicar el outbox (ADR-001 §2.8): amqp://usuario:clave@host:5672/. Sin ella no se publica
    # nada y los eventos esperan en outbox_events hasta que esté.
    rabbitmq_url: str | None = None
    rabbitmq_exchange: str = "payments.events"
    outbox_poll_interval_seconds: float = 2.0
    outbox_batch_size: int = 50


def async_database_url(url: str) -> str:
    """La misma base con el driver async (`postgresql+asyncpg`), conservando credenciales escapadas."""
    parsed = make_url(url)
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def get_settings() -> Settings:
    """Se lee en cada llamada: los tests cambian DATABASE_URL con monkeypatch."""
    return Settings()
