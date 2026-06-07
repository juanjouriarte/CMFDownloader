from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import calendar
from datetime import date

from sqlalchemy import select

from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.carteras_fi import CarteraFINac
from src.db.models.fondos_inversion import FondoInversion
from src.http import make_session
from src.investmentFunds.loaders.carteras import (
    load_carteras, parse_ext, parse_fut_fw, parse_met_part, parse_nac,
)
from src.investmentFunds.loaders.utils import mark_has_data

BASE_URL  = "https://www.cmfchile.cl/institucional/inc/inf_financiera/ifrs_xml"
BACKFILL_START = date(2020, 3, 1)
QUARTER_MONTHS = (3, 6, 9, 12)

ENDPOINTS = {
    "nac":      "ifrs_cartera_nac.php",
    "ext":      "ifrs_cartera_ext.php",
    "met_part": "ifrs_cartera_met_part.php",
    "fut_fw":   "ifrs_cartera_fut_fw.php",
}


def _quarter_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _iter_quarters(start: date, end: date):
    year = start.year
    for month in QUARTER_MONTHS:
        if date(year, month, 1) >= start:
            break
    while (year, month) <= (end.year, end.month):
        yield year, month
        idx = QUARTER_MONTHS.index(month)
        if idx == len(QUARTER_MONTHS) - 1:
            month, year = QUARTER_MONTHS[0], year + 1
        else:
            month = QUARTER_MONTHS[idx + 1]


def _already_loaded(run_fondo: str, periodo: date) -> bool:
    with SessionLocal() as s:
        return s.execute(
            select(CarteraFINac).where(
                CarteraFINac.run_fondo == run_fondo,
                CarteraFINac.periodo == periodo,
            ).limit(1)
        ).first() is not None


def _fetch_fund(fund: FondoInversion, quarters: list[tuple[int, int]],
                fast: bool, force: bool) -> DownloadResult:
    run_fondo = fund.run_fondo
    vigente   = fund.vigente if fund.vigente is not None else True
    session   = make_session(headers={"User-Agent": "Mozilla/5.0"})
    result    = DownloadResult()

    for year, month in quarters:
        periodo = _quarter_end(year, month)
        periodo_str = f"{year}{month:02d}"

        if not force and _already_loaded(run_fondo, periodo):
            result += DownloadResult(skipped=1)
            continue

        try:
            responses = {}
            for tipo, endpoint in ENDPOINTS.items():
                url = f"{BASE_URL}/{endpoint}?rut={run_fondo}&periodo={periodo_str}"
                for attempt in range(1, 4):
                    try:
                        resp = session.get(url, timeout=30)
                        resp.raise_for_status()
                        responses[tipo] = resp.text
                        break
                    except Exception:
                        if attempt < 3:
                            time.sleep(2 ** attempt)

            nac      = parse_nac(responses.get("nac", ""), run_fondo, periodo)
            ext      = parse_ext(responses.get("ext", ""), run_fondo, periodo)
            met_part = parse_met_part(responses.get("met_part", ""), run_fondo, periodo)
            fut_fw   = parse_fut_fw(responses.get("fut_fw", ""), run_fondo, periodo)

            rows = load_carteras(nac, ext, met_part, fut_fw, run_fondo, periodo)

            if rows:
                mark_has_data(run_fondo, True)
            elif not vigente:
                mark_has_data(run_fondo, False)

            result += DownloadResult(downloaded=1, rows_upserted=rows)

        except Exception as exc:
            import traceback
            logger = __import__('logging').getLogger(__name__)
            logger.warning("Error %s %d-%02d: %s\n%s", run_fondo, year, month, exc, traceback.format_exc())
            result += DownloadResult(errors=1)

        time.sleep(random.uniform(0.05, 0.15) if fast else random.uniform(0.3, 0.8))

    return result


class CarterasFIDownloader(BaseDownloader):
    """Descarga carteras de inversión trimestrales de todos los FI desde CMF IFRS."""

    def __init__(self, force: bool = False) -> None:
        super().__init__(output_dir=DOWNLOADS_DIR / "carteras_fi", force=force)

    def run(self) -> DownloadResult:
        today = date.today()
        last_quarter = max((m for m in QUARTER_MONTHS if m <= today.month), default=12)
        year = today.year if last_quarter <= today.month else today.year - 1
        return self._download_all(date(year, last_quarter, 1), today,
                                  only_vigentes=True, workers=1, fast=False)

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill carteras FI desde %s", from_date)
        return self._download_all(from_date, date.today(),
                                  only_vigentes=False, workers=5, fast=True)

    def _download_all(self, from_date: date, to_date: date,
                      only_vigentes: bool, workers: int, fast: bool) -> DownloadResult:
        funds    = self._get_funds(only_vigentes)
        quarters = list(_iter_quarters(from_date, to_date))
        total    = DownloadResult()
        lock     = threading.Lock()
        done     = [0]

        def process(fund):
            return _fetch_fund(fund, quarters, fast, self.force)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, f): f for f in funds}
            for future in as_completed(futures):
                result = future.result()
                with lock:
                    total += result
                    done[0] += 1
                    if done[0] % 100 == 0:
                        self.logger.info("[%d/%d] %s", done[0], len(funds), total)

        return total

    def _get_funds(self, only_vigentes: bool) -> list[FondoInversion]:
        with SessionLocal() as s:
            q = select(FondoInversion).where(FondoInversion.has_data.is_not(False))
            if only_vigentes:
                q = q.where(FondoInversion.vigente == True)
            funds = s.execute(q).scalars().all()
            for f in funds:
                s.expunge(f)
            return funds
