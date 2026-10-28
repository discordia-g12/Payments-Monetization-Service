# Payments-Monetization-Service

Servicio de pagos y monetización de Discordia (Python 3.12, FastAPI, SQLAlchemy y PostgreSQL). Acá van a vivir
el plan Premium, la billetera de puntos y las mejoras (boosts) de servidor.

**Estado actual: es un esqueleto.** Tiene la app de FastAPI, los health checks contra PostgreSQL, los tests y
el pipeline de CI/CD, pero todavía no tiene funcionalidad (`app/api/v1`, `models`, `services` y demás están
vacíos). El Gateway ya le enruta `/api/v1/payments`, `/api/v1/points` y `/api/v1/boosts`: hoy esas rutas
responden `404`.

## Requisitos

- Python 3.12 (la versión de la imagen y del CI).
- Docker, para PostgreSQL.

## Levantarlo en local

### Todo en contenedores

```bash
docker compose up -d --build
curl localhost:8003/readyz        # {"status":"ready"}
```

`docker-compose.yml` levanta la API en `localhost:8003` (Swagger en `/docs`), con recarga en caliente (monta
el repo en el contenedor), y PostgreSQL en `localhost:5435` (usuario y contraseña `postgres`, base
`payments_db`). Bajarlo: `docker compose down` (con `-v` también borra los datos).

### Desde el host

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime + test, lint y SAST

docker compose up -d payments_db
export DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5435/payments_db
uvicorn app.main:app --reload --port 8003
```

El servicio no lee `.env`: `DATABASE_URL` va exportada.

### Con el resto del sistema

Para levantarlo junto con el Gateway y los demás servicios, usar el compose del orquestador
(`discordia-orchestrator/deploy/docker-compose.yml`, profile `local`). Ahí se llama `payments-service`,
publica el 8003 y usa la base `payments_db` del Postgres compartido (en producción, el RDS).

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL, en formato de SQLAlchemy (`postgresql+psycopg2://usuario:clave@host:puerto/base`). Sin ella, `/readyz` responde `503` |

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Mensaje de bienvenida |
| `GET` | `/livez` | El proceso está vivo, sin I/O |
| `GET` | `/readyz` | `SELECT 1` contra PostgreSQL (timeout de 3 s). `503` si falta `DATABASE_URL` o la base no responde |

El contrato completo (OpenAPI) lo genera FastAPI: `/docs` y `/openapi.json`.

## Tests, lint y seguridad

Los mismos comandos que corre el CI:

```bash
ruff check .          # lint (configuración en pyproject.toml)
bandit -r app -ll     # SAST
pytest -v
```

El test de `/readyz` con una base real se saltea si no hay `DATABASE_URL`. Para correrlo, levantar
`payments_db` y exportar la variable como en "Desde el host".

## Migraciones

Alembic está empezado (`alembic/env.py`) pero falta `alembic.ini` y todavía no hay migraciones: hay que
completarlo junto con los primeros modelos.

## Estructura

```text
app/
├── main.py      app de FastAPI, /livez y /readyz
├── api/v1/      (vacío) rutas
├── models/, schemas/, repositories/, services/, core/, db/   (vacíos)
alembic/         env.py, sin migraciones todavía
tests/
```

## CI/CD

`.github/workflows/ci.yml`, en cada push y PR a `main` y `develop`:

1. `test`: ruff, bandit y pytest, contra un PostgreSQL 18 de servicio.
2. `docker`: construye la imagen; en push la publica como `ghcr.io/discordia-g12/payments-service:<sha>`.
3. `deploy`: en push a `main`, la despliega en el nodo **infra** con el workflow del orquestador. Si no queda
   sana, vuelve sola a la versión anterior.

La imagen de producción corre uvicorn sin `--reload`; el compose de este repo lo agrega solo para
desarrollo.
