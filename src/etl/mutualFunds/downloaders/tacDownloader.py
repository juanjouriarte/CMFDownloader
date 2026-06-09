from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.tac import Tac
from src.http import make_session
from src.etl.mutualFunds.loaders.tac import load_tac

BACKFILL_START = date(2020, 1, 1)


def _iter_months(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month > 12:
            month, year = 1, year + 1


class TacDownloader(BaseDownloader):
    """Descarga e ingesta la TAC (Tasa de Administración de Costos) de FM desde CMF."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "tac", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        last = self._last_period_in_db()
        from_date = date(last.year, last.month, 1) if last else BACKFILL_START
        return self._download_range(from_date, date.today())

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill TAC desde %s", from_date)
        return self._download_range(from_date, date.today())

    def _download_range(self, from_date: date, to_date: date) -> DownloadResult:
        months = list(_iter_months(from_date, to_date))
        total = DownloadResult()
        for i, (year, month) in enumerate(months, 1):
            self.logger.info("[%d/%d] Descargando TAC %d-%02d", i, len(months), year, month)
            total += self._fetch_and_load(year, month)
            if i < len(months):
                time.sleep(1)
        return total

    def _fetch_and_load(self, year: int, month: int) -> DownloadResult:
        dest = self.output_dir / f"tac_{year}{month:02d}.xls"

        if self._should_skip(dest) and dest.exists() and dest.stat().st_size > 100:
            self.logger.debug("TAC %d-%02d: ya existe — cargando", year, month)
            rows = load_tac(dest, year, month)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session(headers={"Referer": CMFUrl.TAC_PAGE})
        session.get(CMFUrl.TAC_PAGE, timeout=15)

        resp = session.post(
            CMFUrl.TAC_POST,
            data={"admins": "0", "tipofondo": "0", "moneda": "0",
                  "mes2": f"{month:02d}", "anno2": str(year)},
            timeout=60,
        )
        resp.raise_for_status()

        if not resp.content or len(resp.content) < 500 or b"html" in resp.content[:100].lower():
            self.logger.warning("TAC %d-%02d: sin datos", year, month)
            return DownloadResult(skipped=1)

        dest.write_bytes(resp.content)
        self.logger.info("TAC %d-%02d: %.0f KB descargados", year, month, len(resp.content) / 1024)

        rows = load_tac(dest, year, month)
        dest.unlink(missing_ok=True)
        return DownloadResult(downloaded=1, rows_upserted=rows)

    def _last_period_in_db(self) -> date | None:
        with SessionLocal() as session:
            return session.execute(select(func.max(Tac.periodo))).scalar_one_or_none()
