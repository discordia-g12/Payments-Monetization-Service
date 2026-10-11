from fastapi import FastAPI, Response, status
import os
from sqlalchemy import create_engine, text

app = FastAPI(title="Payments & Monetization Service")

@app.get("/")
def read_root():
    return {"message": "Welcome to Payments & Monetization Service"}

@app.get("/livez", tags=["Health"])
def livez():
    return {"status": "alive"}

@app.get("/readyz", tags=["Health"])
def readyz(response: Response):
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "error": "DATABASE_URL not set"}
    try:
        # Check database connectivity with a short timeout
        engine = create_engine(db_url, connect_args={'connect_timeout': 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "error": str(e)}
