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
from src.db.models.aportantes_fi import CuotasFI
from src.db.models.fondos_inversion import FondoInversion
from src.http import fetch, make_session
from src.investmentFunds.loaders.aportantes import load_aportantes, parse_html
from src.investmentFunds.loaders.entidades import refresh_entidades
from src.investmentFunds.loaders.utils import mark_has_data

BASE_URL       = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
BACKFILL_START = date(2020, 3, 1)
QUARTER_MONTHS = (3, 6, 9, 12)


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


def _tipo(rescatable: bool) -> str:
    return "FIRES" if rescatable else "FINRE"


def _vig(vigente: bool) -> str:
    return "VI" if vigente else "NV"


def _already_loaded(run_fondo: str, periodo: date) -> bool:
    with SessionLocal() as s:
        return s.execute(
            select(CuotasFI).where(
                CuotasFI.run_fondo == run_fondo,
                CuotasFI.periodo == periodo,
            )
        ).first() is not None


def _fetch_fund(fund: FondoInversion, months: list[tuple[int, int]],
                fast: bool, force: bool) -> DownloadResult:
    run_fondo  = fund.run_fondo
    rescatable = fund.rescatable if fund.rescatable is not None else True
    vigente    = fund.vigente if fund.vigente is not None else True
    url = (
        f"{BASE_URL}?mercado=V&rut={run_fondo}&grupo=&tipoentidad={_tipo(rescatable)}"
        f"&vig={_vig(vigente)}&control=svs&pestania=27"
    )
    session = make_session(headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": BASE_URL,
    })
    result = DownloadResult()

    for year, month in months:
        periodo = _quarter_end(year, month)

        if not force and _already_loaded(run_fondo, periodo):
            result += DownloadResult(skipped=1)
            continue

        try:
            resp = fetch(session, url, method="POST",
                         data=f"mm={month:02d}&aa={year}&rut={run_fondo}",
                         timeout=30)
            aportantes, cuotas = parse_html(resp.text, run_fondo, periodo)
            rows = load_aportantes(aportantes, cuotas)
            if rows:
                mark_has_data(run_fondo, True)
            elif not vigente:
                mark_has_data(run_fondo, False)
            result += DownloadResult(downloaded=1, rows_upserted=rows)
        except Exception:
            result += DownloadResult(errors=1)

        time.sleep(random.uniform(0.05, 0.15) if fast else random.uniform(0.3, 0.8))

    return result


class AportantesDownloader(BaseDownloader):
    """Descarga aportantes y cuotas trimestrales de todos los Fondos de Inversión desde CMF."""

    def __init__(self, force: bool = False) -> None:
        super().__init__(output_dir=DOWNLOADS_DIR / "aportantes_fi", force=force)

    def run(self) -> DownloadResult:
        today = date.today()
        last_quarter = max((m for m in QUARTER_MONTHS if m <= today.month), default=12)
        year = today.year if last_quarter <= today.month else today.year - 1
        result = self._download_all(date(year, last_quarter, 1), today,
                                    only_vigentes=True, workers=1, fast=False)
        refresh_entidades()
        return result

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill aportantes FI desde %s", from_date)
        result = self._download_all(from_date, date.today(),
                                    only_vigentes=False, workers=5, fast=True)
        refresh_entidades()
        return result

    def _download_all(self, from_date: date, to_date: date,
                      only_vigentes: bool, workers: int, fast: bool) -> DownloadResult:
        funds  = self._get_funds(only_vigentes)
        months = list(_iter_quarters(from_date, to_date))
        total  = DownloadResult()
        lock   = threading.Lock()
        done   = [0]

        def process(fund):
            return _fetch_fund(fund, months, fast, self.force)

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
