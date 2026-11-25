# Payments-Monetization-Service

Servicio de pagos y monetización de Discordia (Python 3.12, FastAPI, SQLAlchemy y PostgreSQL). Acá van a vivir
el plan Premium, la billetera de puntos y las mejoras (boosts) de servidor.

**Estado actual:** tiene la app de FastAPI con SQLAlchemy async (asyncpg), los health checks, las migraciones
de Alembic con el esquema base (ver [Modelo de datos](#modelo-de-datos)), los tests y el pipeline de CI/CD.
Funcionalidad: **comprar puntos** (con pasarela simulada y saga de compra), saldo e historial. Boosts y Premium
vienen después. El Gateway le enruta `/api/v1/payments`, `/api/v1/points` y `/api/v1/boosts`.

Decisiones de diseño (persistencia, billetera, concurrencia, pasarela, boosts y beneficios):
[ADR-001](docs/ADR-001-Puntos-y-Boosts.md).

## Requisitos

- Docker. Con eso alcanza: la API, la base y los tests corren en contenedores (no hace falta instalar Python
  ni las dependencias).
- Opcional, para trabajar desde el host: Python 3.12 (la versión de la imagen y del CI).

## Levantarlo en local

### Todo en contenedores

```bash
docker compose up -d --build                     # API + base de desarrollo; la API migra al arrancar
curl localhost:8003/readyz                       # {"status":"ready"}
docker compose logs -f payments_api              # logs
docker compose exec payments_api alembic current # cualquier comando dentro del contenedor
docker compose down                              # bajar (con -v también borra los datos)
```

La imagen de desarrollo es la etapa `dev` del `Dockerfile` (trae pytest, ruff y bandit) y monta el repo en
`/app`: los cambios de código se ven sin reconstruir. Si cambian `requirements*.txt`, agregar `--build`.

`docker-compose.yml` levanta la API en `localhost:8003` (Swagger en `/docs`), con recarga en caliente (monta
el repo en el contenedor) y las migraciones aplicadas al arrancar, y PostgreSQL en `localhost:5435` (usuario y contraseña `postgres`, base
`payments_db`). Bajarlo: `docker compose down` (con `-v` también borra los datos).

### Desde el host

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime + test, lint y SAST

docker compose up -d payments_db
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5435/payments_db
alembic upgrade head
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
| `DATABASE_URL` | — | PostgreSQL, en formato de SQLAlchemy (`postgresql+asyncpg://usuario:clave@host:puerto/base`). Se acepta otro driver (`postgresql+psycopg2://`, `postgresql://`): el servicio y Alembic siempre usan asyncpg. Sin ella, `/readyz` responde `503` |

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Mensaje de bienvenida |
| `GET` | `/livez` | El proceso está vivo, sin I/O |
| `GET` | `/readyz` | `SELECT 1` contra PostgreSQL (timeout de 3 s). `503` si falta `DATABASE_URL` o la base no responde (el detalle va al log) |
| `GET` | `/api/v1/points/packages` | Paquetes de puntos a la venta |
| `POST` | `/api/v1/points/purchases` | Comprar un paquete: `{package_id, card_number}` y header `Idempotency-Key`. `201` acreditado, `402` rechazado, `503` sin confirmar o devuelto ([ADR-001 §2.11](docs/ADR-001-Puntos-y-Boosts.md)) |
| `GET` | `/api/v1/points/wallet` | Saldo e historial, del más nuevo al más viejo (`?limit=&before=`) |

Las rutas de `/api/v1` necesitan `X-User-Id`, que pone Kong al validar el token (sin él, `401`). Los errores
son `application/problem+json` con un `code` estable. Tarjetas de prueba: `4242 4242 4242 4242` (aprobada),
`4000 0000 0000 0002` (`card_declined`) y `4000 0000 0000 9995` (`insufficient_funds`).

Para probarlo a mano con el compose de este repo (sin Kong, el header va a mano):

```bash
curl -s -X POST localhost:8003/api/v1/points/purchases \
  -H 'X-User-Id: u1' -H "Idempotency-Key: $(uuidgen)" -H 'Content-Type: application/json' \
  -d '{"package_id": "points_500", "card_number": "4242 4242 4242 4242"}'
curl -s localhost:8003/api/v1/points/wallet -H 'X-User-Id: u1'
```

El contrato completo (OpenAPI) lo genera FastAPI: `/docs` y `/openapi.json`.

## Tests, lint y seguridad

En Docker, contra un PostgreSQL 18 propio de los tests (en memoria, aparte de la base de desarrollo):

```bash
docker compose --profile test run --rm --build tests            # todo lo del CI: ruff, bandit, alembic check, pytest
docker compose --profile test run --rm tests pytest -v -k boost # un subconjunto
docker compose --profile test run --rm tests ruff check --fix . # arreglar el lint
docker compose --profile test down                              # bajar la base de tests
```

Desde el host, los mismos comandos que corre el CI:

```bash
ruff check .          # lint (configuración en pyproject.toml)
bandit -r app -ll     # SAST
pytest -v
```

Los tests de cada historia (`test_points_purchase.py`, `test_boosts.py`, `test_boost_benefits.py`,
`test_payment_gateway.py`, `test_outbox.py`) están escritos como esqueleto: un test por criterio de aceptación,
marcados con `skip` hasta implementar la historia. Los fixtures compartidos (base limpia por test, cliente HTTP,
headers de Kong, Server-Roles falso) están en `tests/conftest.py`.

Los tests de migraciones (`tests/test_migrations.py`) y el de `/readyz` con una base real necesitan PostgreSQL:
se saltean si no hay `DATABASE_URL`. Para correrlos, levantar `payments_db` y exportar la variable como en
"Desde el host". **Ojo:** los de migraciones bajan la base a cero (`alembic downgrade base`) y la vuelven a
migrar: no correrlos contra una base con datos que importen.

## Migraciones

Alembic async (`alembic.ini`, `alembic/env.py`). La URL sale de `DATABASE_URL`, como la app. La imagen corre
`alembic upgrade head` antes de arrancar: si una migración falla, el contenedor no queda sano y el deploy
vuelve solo al tag anterior.

```bash
alembic upgrade head                                     # aplicar
alembic revision --autogenerate -m "describe el cambio"  # crear una (revisar el archivo generado)
alembic downgrade -1                                     # deshacer la última
alembic check                                            # ¿los modelos coinciden con la base migrada?
```

Autogenerate no detecta los `CHECK` ni los índices parciales: esos se escriben a mano en la migración, con los
nombres de la convención de `app/db/base.py`. El CI corre `alembic check` (y `tests/test_migrations.py`), así
que un modelo cambiado sin su migración rompe el build.

## Modelo de datos

Migración inicial: `alembic/versions/20261124_b7e2c4a91d30_initial_schema.py`. Los usuarios se identifican con
el id canónico que pone Kong en `X-User-Id`; los servidores, con el id de Server-Roles. No hay claves foráneas
a otros servicios.

| Tabla | Qué guarda |
|---|---|
| `wallets` | Saldo de puntos por usuario (`CHECK balance >= 0`). |
| `point_transactions` | Historial de movimientos, solo de agregar: `purchase` (crédito, con paquete, precio y comprobante del pago, único), `boost` (débito, apunta al boost), `refund`, `adjustment`. Guarda el saldo que quedó después de cada uno. |
| `server_boosts` | Cada boost es una unidad: servidor, quién, cuántos puntos costó. Retirarlo pone `removed_at` (sin reembolso). |
| `server_boost_counters` | Boosts vigentes y nivel (0 a 3) de cada servidor. |
| `subscriptions` | Plan Premium (`monthly` / `yearly`), estado y período. A lo sumo una viva (`active` o `past_due`) por usuario (índice único parcial). |
| `point_purchases` | Cada compra de puntos, con el estado de su saga (`pending`, `completed`, `failed`, `refunded`). Única por usuario e `Idempotency-Key`: un reintento no cobra dos veces. |
| `idempotency_records` | Respuestas guardadas por `Idempotency-Key` (por usuario) para lo que no es una compra (boostear). |
| `outbox_events` | Transactional Outbox: los eventos (`PaymentCompleted`, `ServerBoosted`, `PremiumSubscribed`, ...) se guardan en la misma transacción que el cambio y un worker los publica en RabbitMQ. |

**Aislamiento.** Todo lo que mueve puntos bloquea primero la fila con `SELECT ... FOR UPDATE`, en la misma
transacción en la que escribe el movimiento y el evento del outbox:

- La billetera del usuario: dos compras o boosts a la vez del mismo usuario se ordenan, y el saldo nunca queda
  negativo (además lo impide el `CHECK`).
- El contador del servidor: dos boosts a la vez del mismo servidor se ordenan, y el que cruza un umbral de nivel
  (5, 15, 30) se detecta una sola vez.

Si una operación necesita las dos, bloquea primero la billetera y después el contador, siempre en ese orden,
para no generar deadlocks. Una compra bloquea primero su propia fila de `point_purchases` y después la
billetera.

## Estructura

```text
app/
├── main.py      app de FastAPI, /livez y /readyz
├── core/        configuración (DATABASE_URL)
├── db/          Base con la convención de nombres y la sesión async
├── models/      tablas (ver Modelo de datos)
├── api/v1/, schemas/, repositories/, services/   (vacíos)
alembic/         migraciones (alembic.ini en la raíz)
tests/
```

## CI/CD

`.github/workflows/ci.yml`, en cada push y PR a `main` y `develop`:

1. `test`: ruff, bandit, `alembic upgrade head` + `alembic check` y pytest, contra un PostgreSQL 18 de servicio.
2. `docker`: construye la imagen; en push la publica como `ghcr.io/discordia-g12/payments-service:<sha>`.
3. `deploy`: en push a `main`, la despliega en el nodo **infra** con el workflow del orquestador. Si no queda
   sana, vuelve sola a la versión anterior.

La imagen de producción corre uvicorn sin `--reload`; el compose de este repo lo agrega solo para
desarrollo.
