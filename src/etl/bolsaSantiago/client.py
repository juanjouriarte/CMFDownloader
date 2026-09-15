from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from src.config import BOLSA_COOKIES, BOLSA_CSRF
from src.http import make_session

BOLSA_BASE_URL = "https://www.bolsadesantiago.com"
QUOTE_URL = f"{BOLSA_BASE_URL}/api/RV_Instrumentos/getResumenPrecios"


class BolsaError(RuntimeError):
    """Base error for read-only Bolsa de Santiago market-data requests."""


class BolsaNotConfiguredError(BolsaError):
    pass


class BolsaAuthenticationError(BolsaError):
    pass


class BolsaUpstreamError(BolsaError):
    pass


class BolsaQuoteNotFoundError(BolsaError):
    pass


def make_bolsa_session() -> requests.Session:
    if not BOLSA_COOKIES or not BOLSA_CSRF:
        raise BolsaNotConfiguredError(
            "Bolsa de Santiago credentials are not configured"
        )
    return make_session(headers={
        "accept": "application/json, text/plain, */*",
        "accept-language": "es-CL,es;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "content-type": "application/json;charset=UTF-8",
        "origin": BOLSA_BASE_URL,
        "pragma": "no-cache",
        "referer": f"{BOLSA_BASE_URL}/",
        "x-csrf-token": BOLSA_CSRF,
        "cookie": BOLSA_COOKIES,
    })


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _quantity(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def get_live_quote(nemotecnico: str) -> dict[str, Any]:
    """Return the current best bid and ask published by Bolsa de Santiago."""
    session = make_bolsa_session()
    try:
        try:
            response = session.post(
                QUOTE_URL,
                json={"nemo": nemotecnico},
                timeout=30,
                allow_redirects=False,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise BolsaUpstreamError("Bolsa de Santiago is unavailable") from exc

        if response.status_code in {401, 403}:
            raise BolsaAuthenticationError(
                "Bolsa de Santiago session is expired or unauthorized"
            )
        if response.status_code != 200:
            raise BolsaUpstreamError(
                f"Bolsa de Santiago returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BolsaUpstreamError(
                "Bolsa de Santiago returned an invalid response"
            ) from exc
    finally:
        session.close()

    results = payload.get("listaResult") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise BolsaUpstreamError("Bolsa de Santiago response has an invalid schema")

    quote = next(
        (
            row for row in results
            if isinstance(row, dict) and row.get("tipo_dato") == "puntas"
        ),
        None,
    )
    if quote is None:
        raise BolsaQuoteNotFoundError(
            f"No quote was found for nemotecnico {nemotecnico}"
        )

    bid_price = _positive_number(quote.get("compra"))
    ask_price = _positive_number(quote.get("venta"))
    midpoint = None
    spread = None
    spread_pct = None
    if bid_price is not None and ask_price is not None:
        midpoint = (bid_price + ask_price) / 2
        spread = ask_price - bid_price
        if midpoint:
            spread_pct = spread / midpoint * 100

    if bid_price is not None and ask_price is not None:
        status = "two_sided"
    elif bid_price is not None:
        status = "bid_only"
    elif ask_price is not None:
        status = "ask_only"
    else:
        status = "no_quotes"

    return {
        "nemotecnico": nemotecnico,
        "status": status,
        "bid_price": bid_price,
        "bid_quantity": _quantity(quote.get("cantidad_com")),
        "ask_price": ask_price,
        "ask_quantity": _quantity(quote.get("cantidad_ven")),
        "midpoint": round(midpoint, 6) if midpoint is not None else None,
        "spread": round(spread, 6) if spread is not None else None,
        "spread_pct": round(spread_pct, 6) if spread_pct is not None else None,
        "retrieved_at": datetime.now(timezone.utc),
        "source": "Bolsa de Santiago",
    }
