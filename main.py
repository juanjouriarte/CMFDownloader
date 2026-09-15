from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.etl.financialStatements.api import router as financial_statements_router
from src.api import router as public_api_router
from src.db.engine import engine

app = FastAPI(title="CMF Downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(financial_statements_router)
app.include_router(public_api_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    """Report whether this process can serve database-backed requests."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logging.getLogger(__name__).warning(
            "Readiness check failed: database unavailable"
        )
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )
    return {"status": "ready", "database": "ok"}
