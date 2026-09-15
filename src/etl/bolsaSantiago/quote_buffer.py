from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from src.etl.bolsaSantiago.client import (
    BolsaAuthenticationError,
    BolsaError,
    BolsaNotConfiguredError,
    BolsaQuoteNotFoundError,
    get_live_quote,
)

logger = logging.getLogger(__name__)

BTG_FIXED_INCOME_FUNDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Deuda Activa Plus",
        ("CFIBTGDAPA", "CFIBTGDAPI", "CFIBTGDAPF", "CFIBTGDAPG"),
    ),
    (
        "Deuda Corporativa Chile",
        ("CFIBPDCCHA", "CFIBPDCCHI", "CFIBPDCCHF", "CFIBPDCCHG"),
    ),
    ("Deuda Estratégica", ("CFIBTGDEA", "CFIBTGDEB", "CFIBTGDEI")),
    ("ETF Renta Fija Chile Mediano Plazo", ("CFIBTETFMP",)),
    ("ETF Renta Fija Chile Largo Plazo", ("CFIETFRFLP",)),
    ("Retorno Estratégico", ("CFIBTGRE",)),
)


def _seconds_from_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %.1f seconds", name, default)
        return default


def _error_code(exc: BolsaError) -> str:
    if isinstance(exc, BolsaNotConfiguredError):
        return "not_configured"
    if isinstance(exc, BolsaAuthenticationError):
        return "authentication_error"
    if isinstance(exc, BolsaQuoteNotFoundError):
        return "quote_not_found"
    return "upstream_error"


class BTGFixedIncomeQuoteBuffer:
    """Single-threaded, rate-limited in-memory quote cache."""

    def __init__(
        self,
        *,
        fetcher: Callable[[str], dict[str, Any]] = get_live_quote,
        pace_seconds: float | None = None,
        cycle_seconds: float | None = None,
    ) -> None:
        self._fetcher = fetcher
        self.pace_seconds = (
            pace_seconds
            if pace_seconds is not None
            else _seconds_from_env("BOLSA_QUOTE_PACE_SECONDS", 3.0, 1.0)
        )
        self.cycle_seconds = (
            cycle_seconds
            if cycle_seconds is not None
            else _seconds_from_env("BOLSA_QUOTE_CYCLE_SECONDS", 900.0, 60.0)
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._refreshing = False
        self._cycle_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._last_cycle_error: str | None = None

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(
            ticker
            for _, tickers in BTG_FIXED_INCOME_FUNDS
            for ticker in tickers
        )

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="btg-fixed-income-quotes",
                daemon=True,
            )
            self._thread.start()
        logger.info(
            "Started BTG fixed-income quote buffer: %d tickers, %.1fs pace, %.1fs cycle",
            len(self.tickers),
            self.pace_seconds,
            self.cycle_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=min(self.pace_seconds + 1, 10))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self.cycle_seconds)

    def refresh_once(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
            self._cycle_started_at = datetime.now(timezone.utc)
            self._last_cycle_error = None

        completed = True
        try:
            for index, ticker in enumerate(self.tickers):
                if self._stop.is_set():
                    completed = False
                    break
                attempted_at = datetime.now(timezone.utc)
                try:
                    quote = self._fetcher(ticker)
                except BolsaError as exc:
                    error = _error_code(exc)
                    logger.warning("Bolsa quote %s failed: %s", ticker, error)
                    with self._lock:
                        previous = self._entries.get(ticker, {})
                        self._entries[ticker] = {
                            "quote": previous.get("quote"),
                            "last_attempted_at": attempted_at,
                            "last_error": error,
                        }
                        self._last_cycle_error = error
                    if isinstance(
                        exc, (BolsaNotConfiguredError, BolsaAuthenticationError)
                    ):
                        completed = False
                        break
                except Exception:
                    logger.exception("Unexpected Bolsa quote failure for %s", ticker)
                    with self._lock:
                        previous = self._entries.get(ticker, {})
                        self._entries[ticker] = {
                            "quote": previous.get("quote"),
                            "last_attempted_at": attempted_at,
                            "last_error": "internal_error",
                        }
                        self._last_cycle_error = "internal_error"
                else:
                    with self._lock:
                        self._entries[ticker] = {
                            "quote": quote,
                            "last_attempted_at": attempted_at,
                            "last_error": None,
                        }

                if index < len(self.tickers) - 1 and self._stop.wait(
                    self.pace_seconds
                ):
                    completed = False
                    break
        finally:
            with self._lock:
                if completed:
                    self._last_completed_at = datetime.now(timezone.utc)
                self._refreshing = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            entries = {
                ticker: {
                    "quote": entry.get("quote"),
                    "last_attempted_at": entry.get("last_attempted_at"),
                    "last_error": entry.get("last_error"),
                }
                for ticker, entry in self._entries.items()
            }
            refreshing = self._refreshing
            cycle_started_at = self._cycle_started_at
            last_completed_at = self._last_completed_at
            last_cycle_error = self._last_cycle_error

        available = sum(1 for entry in entries.values() if entry.get("quote"))
        total = len(self.tickers)
        if available == total:
            status = "ready"
        elif available:
            status = "partial"
        else:
            status = "warming"

        funds = []
        for fund_name, tickers in BTG_FIXED_INCOME_FUNDS:
            instruments = []
            for ticker in tickers:
                entry = entries.get(ticker, {})
                quote = entry.get("quote")
                if quote:
                    instrument = dict(quote)
                else:
                    instrument = {
                        "nemotecnico": ticker,
                        "status": "error" if entry.get("last_error") else "pending",
                        "bid_price": None,
                        "bid_quantity": 0,
                        "ask_price": None,
                        "ask_quantity": 0,
                        "midpoint": None,
                        "spread": None,
                        "spread_pct": None,
                        "retrieved_at": None,
                        "source": "Bolsa de Santiago",
                    }
                instrument["last_attempted_at"] = entry.get("last_attempted_at")
                instrument["last_error"] = entry.get("last_error")
                instruments.append(instrument)
            funds.append({"name": fund_name, "instruments": instruments})

        return {
            "status": status,
            "refreshing": refreshing,
            "total_instruments": total,
            "available_quotes": available,
            "pace_seconds": self.pace_seconds,
            "cycle_seconds": self.cycle_seconds,
            "cycle_started_at": cycle_started_at,
            "last_completed_at": last_completed_at,
            "last_cycle_error": last_cycle_error,
            "funds": funds,
        }


btg_fixed_income_quote_buffer = BTGFixedIncomeQuoteBuffer()
