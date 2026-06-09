from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.financial_statements import FinancialStatement
from src.http import make_session
from src.etl.financialStatements.loaders.financial_statements import load_financial_statements

# Download ranges following CMF availability pattern:
#   - Single months for recent incomplete years (2026-03, 2025-03 to 2025-12)
#   - Full yearly ranges for complete years (2024 back to 2009)
DOWNLOAD_RANGES: list[tuple[str, str]] = (
    [("202603", "202603")]
    + [(f"2025{m:02d}", f"2025{m:02d}") for m in [3, 6, 9, 12]]
    + [(f"{y}03", f"{y}12") for y in range(2024, 2008, -1)]
)


class FinancialStatementsDownloader(BaseDownloader):
    """Downloads IFRS financial statements for all CMF-supervised companies."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "financial_statements", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        last = self._last_periodo_in_db()
        ranges = DOWNLOAD_RANGES
        if last and not self.force:
            # Skip ranges whose termino period is already loaded
            ranges = [(i, t) for i, t in ranges if _ym_to_date(t) > last]
        if not ranges:
            self.logger.info("Financial statements already up to date (last: %s)", last)
            return DownloadResult(skipped=1)
        return self._download_ranges(ranges)

    def backfill(self) -> DownloadResult:
        self.logger.info("Backfill financial statements — %d ranges", len(DOWNLOAD_RANGES))
        return self._download_ranges(DOWNLOAD_RANGES)

    def download_period(self, inicio: str, termino: str) -> DownloadResult:
        """Download a single period range (YYYYMM → YYYYMM). Used by the API."""
        self.logger.info("Downloading financial statements %s → %s", inicio, termino)
        return self._fetch_and_load(inicio, termino)

    def _download_ranges(self, ranges: list[tuple[str, str]]) -> DownloadResult:
        total = DownloadResult()
        for i, (inicio, termino) in enumerate(ranges, 1):
            self.logger.info("[%d/%d] Downloading %s → %s", i, len(ranges), inicio, termino)
            total += self._fetch_and_load(inicio, termino)
            if i < len(ranges):
                time.sleep(1)
        return total

    def _fetch_and_load(self, inicio: str, termino: str) -> DownloadResult:
        filename = f"fs_{inicio}_{termino}.txt"
        dest = self.output_dir / filename

        if self._should_skip(dest) and dest.exists() and dest.stat().st_size > 100:
            self.logger.debug("%s: already exists — loading", filename)
            rows = load_financial_statements(dest)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session()
        resp = session.get(
            CMFUrl.FINANCIAL_STATEMENTS,
            params={"inicio": inicio, "termino": termino},
            timeout=120,
        )
        resp.raise_for_status()

        if not resp.content or b"no se encuentra" in resp.content[:100].lower():
            self.logger.warning("%s → %s: no data available", inicio, termino)
            return DownloadResult(skipped=1)

        dest.write_bytes(resp.content)
        self.logger.info("%s: %.1f MB downloaded", filename, len(resp.content) / 1024 / 1024)

        rows = load_financial_statements(dest)
        return DownloadResult(downloaded=1, rows_upserted=rows)

    def _last_periodo_in_db(self) -> date | None:
        with SessionLocal() as session:
            return session.execute(select(func.max(FinancialStatement.periodo))).scalar_one_or_none()


def _ym_to_date(yyyymm: str) -> date:
    return date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)
