from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from src.etl.bolsaSantiago.client import (
    BolsaAuthenticationError,
    BolsaNotConfiguredError,
    BolsaQuoteNotFoundError,
    BolsaUpstreamError,
    get_live_quote,
)

router = APIRouter(prefix="/bolsa", tags=["Bolsa de Santiago"])

_NEMOTECNICO_RE = re.compile(r"^[A-Z0-9.$-]{1,30}$")


class LiveQuote(BaseModel):
    nemotecnico: str
    status: Literal["two_sided", "bid_only", "ask_only", "no_quotes"]
    bid_price: float | None
    bid_quantity: int
    ask_price: float | None
    ask_quantity: int
    midpoint: float | None
    spread: float | None
    spread_pct: float | None
    retrieved_at: datetime
    source: str


@router.get(
    "/quotes/{nemotecnico}",
    response_model=LiveQuote,
    summary="Live best bid and ask for a Bolsa de Santiago instrument",
)
def live_quote(nemotecnico: str, response: Response) -> dict:
    normalized = nemotecnico.strip().upper()
    if not _NEMOTECNICO_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Invalid nemotecnico")

    response.headers["Cache-Control"] = "no-store"
    try:
        return get_live_quote(normalized)
    except BolsaQuoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BolsaNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail="Bolsa de Santiago market data is not configured",
        ) from exc
    except BolsaAuthenticationError as exc:
        raise HTTPException(
            status_code=502,
            detail="Bolsa de Santiago session is expired",
        ) from exc
    except BolsaUpstreamError as exc:
        raise HTTPException(
            status_code=502,
            detail="Bolsa de Santiago market data is unavailable",
        ) from exc
