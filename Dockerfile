# Tres etapas. La de producción es la ÚLTIMA (runtime): es la que construye el CI sin --target.
#   base     Python y las dependencias de runtime
#   dev      + test, lint y SAST (requirements-dev.txt). La usan el compose de este repo y los tests en Docker
#   runtime  la imagen que se despliega

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Sin compilador ni libpq: asyncpg trae ruedas precompiladas para Linux
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS dev

COPY requirements-dev.txt /app/
RUN pip install --no-cache-dir -r requirements-dev.txt
# Cachés fuera de /app: en desarrollo /app es el repo montado
ENV RUFF_CACHE_DIR=/tmp/ruff-cache
COPY . /app/
# Sigue como root: el repo montado es del usuario de la máquina, y los tests escriben (.pytest_cache)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]


FROM base AS runtime

COPY . /app/

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000
# Migra antes de arrancar, como Auth y Server-Roles: con una migración rota el contenedor no queda healthy
# y el deploy vuelve solo al tag anterior
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
