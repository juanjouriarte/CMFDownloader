from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.financial_statements import router as financial_statements_router
from src.scheduler import start as start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield


app = FastAPI(title="CMF Downloader", lifespan=lifespan)

app.include_router(financial_statements_router)


@app.get("/health")
def health():
    return {"status": "ok"}
