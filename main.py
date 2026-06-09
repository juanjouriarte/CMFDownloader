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

from src.etl.financialStatements.api import router as financial_statements_router
from src.api import router as public_api_router

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
