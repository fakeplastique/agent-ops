from fastapi import FastAPI

from app.api import health

app = FastAPI(title="Agent Ops")
app.include_router(health.router)
