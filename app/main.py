from fastapi import FastAPI

app = FastAPI(title="Payments & Monetization Service")

@app.get("/")
def read_root():
    return {"message": "Welcome to Payments & Monetization Service"}
