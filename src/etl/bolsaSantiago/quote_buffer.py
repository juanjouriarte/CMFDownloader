from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
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
    """On-demand, rate-limited in-memory quote cache."""

    def __init__(
        self,
        *,
        fetcher: Callable[[str], dict[str, Any]] = get_live_quote,
        pace_seconds: float | None = None,
        cache_seconds: float | None = None,
    ) -> None:
        self._fetcher = fetcher
        self.pace_seconds = (
            pace_seconds
            if pace_seconds is not None
            else _seconds_from_env("BOLSA_QUOTE_PACE_SECONDS", 3.0, 1.0)
        )
        self.cache_seconds = (
            cache_seconds
            if cache_seconds is not None
            else _seconds_from_env("BOLSA_QUOTE_CACHE_SECONDS", 60.0, 1.0)
        )
        self._condition = threading.Condition()
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

    def get_or_refresh(self) -> dict[str, Any]:
        """Return a fresh snapshot, refreshing synchronously when expired.

        Only one caller performs the refresh. Other callers arriving while that
        refresh is running wait for it and receive the same cached result.
        """
        with self._condition:
            while self._refreshing:
                self._condition.wait()
            if self._is_fresh(datetime.now(timezone.utc)):
                return self._snapshot_locked()
            self._begin_refresh_locked()

        self._perform_refresh()
        return self.snapshot()

    def refresh_once(self) -> None:
        """Force one synchronized refresh, primarily for operations and tests."""
        with self._condition:
            while self._refreshing:
                self._condition.wait()
            self._begin_refresh_locked()

        self._perform_refresh()

    def _is_fresh(self, now: datetime) -> bool:
        return bool(
            self._last_completed_at
            and now
            < self._last_completed_at + timedelta(seconds=self.cache_seconds)
        )

    def _begin_refresh_locked(self) -> None:
        self._refreshing = True
        self._cycle_started_at = datetime.now(timezone.utc)
        self._last_cycle_error = None

    def _perform_refresh(self) -> None:
        """Fetch every ticker sequentially and release all waiting callers."""

        try:
            for index, ticker in enumerate(self.tickers):
                attempted_at = datetime.now(timezone.utc)
                try:
                    quote = self._fetcher(ticker)
                except BolsaError as exc:
                    error = _error_code(exc)
                    logger.warning("Bolsa quote %s failed: %s", ticker, error)
                    with self._condition:
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
                        break
                except Exception:
                    logger.exception("Unexpected Bolsa quote failure for %s", ticker)
                    with self._condition:
                        previous = self._entries.get(ticker, {})
                        self._entries[ticker] = {
                            "quote": previous.get("quote"),
                            "last_attempted_at": attempted_at,
                            "last_error": "internal_error",
                        }
                        self._last_cycle_error = "internal_error"
                else:
                    with self._condition:
                        self._entries[ticker] = {
                            "quote": quote,
                            "last_attempted_at": attempted_at,
                            "last_error": None,
                        }

                if index < len(self.tickers) - 1:
                    time.sleep(self.pace_seconds)
        finally:
            with self._condition:
                # Cache unsuccessful attempts too, preventing repeated calls when
                # credentials expire or Bolsa is temporarily unavailable.
                self._last_completed_at = datetime.now(timezone.utc)
                self._refreshing = False
                self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
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
        cache_expires_at = (
            last_completed_at + timedelta(seconds=self.cache_seconds)
            if last_completed_at
            else None
        )

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
            "cache_seconds": self.cache_seconds,
            "cache_expires_at": cache_expires_at,
            "cycle_started_at": cycle_started_at,
            "last_completed_at": last_completed_at,
            "last_cycle_error": last_cycle_error,
            "funds": funds,
        }


btg_fixed_income_quote_buffer = BTGFixedIncomeQuoteBuffer()
