from __future__ import annotations

import logging
import random
import time
from datetime import date

from sqlalchemy import func, select

from src.base import DownloadResult
from src.config import BOLSA_COOKIES, BOLSA_CSRF
from src.db.engine import SessionLocal
from src.db.models.dividendos import Dividendo
from src.etl.bolsaSantiago.loaders.dividendos import load_dividendos

API_URL    = "https://www.bolsadesantiago.com/api/RV_ResumenMercado/getDividendos"
START_YEAR = 1973

logger = logging.getLogger(__name__)


def _make_session():
    import requests
    session = requests.Session()
    session.headers.update({
        "accept":             "application/json, text/plain, */*",
        "accept-language":    "en-US,en;q=0.9,es;q=0.8",
        "cache-control":      "no-cache",
        "content-type":       "application/json;charset=UTF-8",
        "origin":             "https://www.bolsadesantiago.com",
        "pragma":             "no-cache",
        "referer":            "https://www.bolsadesantiago.com/",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "x-csrf-token":       BOLSA_CSRF,
        "cookie":             BOLSA_COOKIES,
        "sec-ch-ua":          '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
    })
    return session


def _fetch_year(session, year: int) -> list[dict] | None:
    resp = session.post(
        API_URL,
        json={"fec_pagoini": f"{year}-01-01", "fec_pagofin": f"{year}-12-31", "nemo": ""},
        timeout=60,
        allow_redirects=False,
    )
    if resp.status_code != 200:
        logger.error("Dividendos %d: HTTP %d", year, resp.status_code)
        return None
    return resp.json().get("listaResult", [])


class DividendosDownloader:
    """Descarga e ingesta dividendos y variaciones de capital desde Bolsa de Santiago."""

    def __init__(self, force: bool = False) -> None:
        self.force = force
        self.logger = logger

    def run(self) -> DownloadResult:
        last_year = self._last_year_in_db() or START_YEAR - 1
        current_year = date.today().year
        # Bolsa pre-announces some payment dates, so MAX(fec_pago) can already be in
        # a future year — don't let that push the sync window past today.
        last_year = min(last_year, current_year)
        # Always re-fetch the current year (dividends are added throughout the year)
        # and the previous year in case of late postings
        from_year = max(last_year, current_year - 1)
        return self._download_range(from_year, current_year)

    def backfill(self, from_year: int = START_YEAR) -> DownloadResult:
        self.logger.info("Backfill dividendos desde %d", from_year)
        return self._download_range(from_year, date.today().year)

    def _download_range(self, from_year: int, to_year: int) -> DownloadResult:
        if not BOLSA_COOKIES or not BOLSA_CSRF:
            self.logger.error("BOLSA_COOKIES y BOLSA_CSRF no configurados — omitiendo")
            return DownloadResult(errors=1)

        session = _make_session()
        total = DownloadResult()
        years = list(range(from_year, to_year + 1))

        for i, year in enumerate(years, 1):
            self.logger.info("[%d/%d] Descargando dividendos %d", i, len(years), year)
            records = _fetch_year(session, year)

            if records is None:
                self.logger.warning(
                    "Sesión expirada o error en %d — actualiza BOLSA_COOKIES y BOLSA_CSRF", year
                )
                total += DownloadResult(errors=1)
                break

            rows = load_dividendos(records, year)
            total += DownloadResult(downloaded=1, rows_upserted=rows)

            if i < len(years):
                time.sleep(random.uniform(1.0, 2.5))

        return total

    def _last_year_in_db(self) -> int | None:
        with SessionLocal() as session:
            val = session.execute(select(func.max(Dividendo.fec_pago))).scalar_one_or_none()
            if val is None:
                return None
            # fec_pago is stored as a string e.g. "2026-05-15"
            return int(str(val)[:4])
