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


def async_database_url(url: str) -> str:
    """La misma base con el driver async (`postgresql+asyncpg`), conservando credenciales escapadas."""
    parsed = make_url(url)
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def get_settings() -> Settings:
    """Se lee en cada llamada: los tests cambian DATABASE_URL con monkeypatch."""
    return Settings()
