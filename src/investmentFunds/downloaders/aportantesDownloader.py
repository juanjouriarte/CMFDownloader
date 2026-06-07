from __future__ import annotations

import random
import time
from datetime import date

from sqlalchemy import select, func

from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.aportantes_fi import CuotasFI
from src.db.models.fondos_inversion import FondoInversion
from src.http import make_session
from src.investmentFunds.loaders.aportantes import load_aportantes, parse_html
from src.investmentFunds.loaders.utils import mark_has_data

BASE_URL     = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
BACKFILL_START = date(2020, 3, 1)
QUARTER_MONTHS = (3, 6, 9, 12)


def _iter_quarters(start: date, end: date):
    """Yield (year, month) for each quarter-end between start and end."""
    year = start.year
    for month in QUARTER_MONTHS:
        if date(year, month, 1) >= start:
            break
    while (year, month) <= (end.year, end.month):
        yield year, month
        idx = QUARTER_MONTHS.index(month)
        if idx == len(QUARTER_MONTHS) - 1:
            month = QUARTER_MONTHS[0]
            year += 1
        else:
            month = QUARTER_MONTHS[idx + 1]


def _tipo(rescatable: bool) -> str:
    return "FIRES" if rescatable else "FINRE"


def _vig(vigente: bool) -> str:
    return "VI" if vigente else "NV"


class AportantesDownloader(BaseDownloader):
    """Descarga aportantes y cuotas mensuales de todos los Fondos de Inversión desde CMF."""

    def __init__(self, force: bool = False) -> None:
        super().__init__(output_dir=DOWNLOADS_DIR / "aportantes_fi", force=force)

    def run(self) -> DownloadResult:
        today = date.today()
        # Find the most recent completed quarter
        last_quarter = max(m for m in QUARTER_MONTHS if m <= today.month) if any(m <= today.month for m in QUARTER_MONTHS) else 12
        year = today.year if last_quarter <= today.month else today.year - 1
        from_date = date(year, last_quarter, 1)
        return self._download_all(from_date, today, only_vigentes=True)

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill aportantes FI desde %s", from_date)
        return self._download_all(from_date, date.today(), only_vigentes=False)

    def _download_all(self, from_date: date, to_date: date, only_vigentes: bool) -> DownloadResult:
        funds = self._get_funds(only_vigentes)
        months = list(_iter_quarters(from_date, to_date))
        total = DownloadResult()
        session = make_session(headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": BASE_URL,
        })

        for i, fund in enumerate(funds, 1):
            run_fondo = fund.run_fondo
            rescatable = fund.rescatable if fund.rescatable is not None else True
            vigente = fund.vigente if fund.vigente is not None else True
            url = (
                f"{BASE_URL}?mercado=V&rut={run_fondo}&grupo=&tipoentidad={_tipo(rescatable)}"
                f"&vig={_vig(vigente)}&control=svs&pestania=27"
            )

            for year, month in months:
                periodo = date(year, month, 1)

                if not self.force and self._already_loaded(run_fondo, periodo):
                    total += DownloadResult(skipped=1)
                    continue

                for attempt in range(1, 4):
                    try:
                        resp = session.post(
                            url,
                            data=f"mm={month:02d}&aa={year}&rut={run_fondo}",
                            timeout=30,
                        )
                        resp.raise_for_status()
                        aportantes, cuotas = parse_html(resp.text, run_fondo, periodo)
                        rows = load_aportantes(aportantes, cuotas)
                        if rows:
                            mark_has_data(run_fondo, True)
                        elif not vigente:
                            mark_has_data(run_fondo, False)
                        total += DownloadResult(downloaded=1, rows_upserted=rows)
                        break
                    except Exception as exc:
                        if attempt == 3:
                            self.logger.warning("Error %s %d-%02d (3 attempts): %s", run_fondo, year, month, exc)
                            total += DownloadResult(errors=1)
                        else:
                            self.logger.debug("Retry %d %s %d-%02d: %s", attempt, run_fondo, year, month, exc)
                            time.sleep(2 ** attempt)

                time.sleep(random.uniform(0.3, 0.8))

            if i % 100 == 0:
                self.logger.info("[%d/%d] %s", i, len(funds), total)

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

    def _already_loaded(self, run_fondo: str, periodo: date) -> bool:
        with SessionLocal() as s:
            return s.execute(
                select(CuotasFI).where(
                    CuotasFI.run_fondo == run_fondo,
                    CuotasFI.periodo == periodo,
                )
            ).first() is not None
