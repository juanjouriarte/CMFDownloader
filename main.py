from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI

from src.financialStatements.api import router as financial_statements_router

app = FastAPI(title="CMF Downloader")

app.include_router(financial_statements_router)


@app.get("/health")
def health():
    return {"status": "ok"}
