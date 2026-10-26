import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "Payments" in response.json()["message"]


def test_livez():
    response = client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readyz_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["error"] == "DATABASE_URL not set"


def test_readyz_database_unreachable(monkeypatch):
    # Puerto 1: no hay nada escuchando, la conexión falla enseguida
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://user:pass@127.0.0.1:1/payments_db")
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not ready"


# Con una base real: en el CI la levanta el job (servicio postgres). En local se saltea si no hay DATABASE_URL.
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="sin DATABASE_URL (en el CI hay un Postgres)")
def test_readyz_with_database():
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
