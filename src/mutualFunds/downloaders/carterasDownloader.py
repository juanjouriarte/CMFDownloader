from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.carteras import CarteraNaci
from src.http import make_session
from src.mutualFunds.loaders.carteras import load_cartera

CARTERA_TYPES = ["NACI", "EXTR", "OPCI", "FUTU", "OPLA"]
BACKFILL_START = date(2020, 1, 1)
MIN_FILE_BYTES = 50


def _iter_months(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month > 12:
            month, year = 1, year + 1


class CarterasDownloader(BaseDownloader):
    """Descarga e ingesta las carteras de inversión de Fondos Mutuos desde CMF."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "carteras", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        last = self._last_period_in_db()
        from_date = date(last.year, last.month, 1) if last else BACKFILL_START
        return self._download_range(from_date, date.today())

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill carteras desde %s", from_date)
        return self._download_range(from_date, date.today())

    def _download_range(self, from_date: date, to_date: date) -> DownloadResult:
        months = list(_iter_months(from_date, to_date))
        total = DownloadResult()
        for i, (year, month) in enumerate(months, 1):
            self.logger.info("[%d/%d] Procesando %d-%02d", i, len(months), year, month)
            for tipo in CARTERA_TYPES:
                total += self._fetch_and_load(year, month, tipo)
            if i < len(months):
                time.sleep(1)
        return total

    def _fetch_and_load(self, year: int, month: int, tipo: str) -> DownloadResult:
        tipo_dir = self.output_dir / tipo
        tipo_dir.mkdir(exist_ok=True)
        dest = tipo_dir / f"cartera_{tipo}_{year}{month:02d}.txt"

        if self._should_skip(dest) and dest.exists() and dest.stat().st_size > MIN_FILE_BYTES:
            self.logger.debug("%d-%02d %s: ya existe — cargando desde archivo", year, month, tipo)
            rows = load_cartera(dest, tipo, year, month)
            dest.unlink(missing_ok=True)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session(headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": CMFUrl.CARTERAS_PAGE,
        })

        resp = session.post(
            CMFUrl.CARTERAS_POST,
            data={"mm": f"{month:02d}", "aa": str(year), "cartera": tipo},
            timeout=60,
        )
        resp.raise_for_status()

        if not resp.content or len(resp.content) < MIN_FILE_BYTES or "html" in resp.headers.get("Content-Type", ""):
            self.logger.debug("%d-%02d %s: sin datos", year, month, tipo)
            return DownloadResult(skipped=1)

        dest.write_bytes(resp.content)
        self.logger.info("%d-%02d %s: %.0f KB descargados", year, month, tipo, len(resp.content) / 1024)

        rows = load_cartera(dest, tipo, year, month)
        dest.unlink(missing_ok=True)
        return DownloadResult(downloaded=1, rows_upserted=rows)

    def _last_period_in_db(self) -> date | None:
        with SessionLocal() as session:
            return session.execute(select(func.max(CarteraNaci.periodo))).scalar_one_or_none()
