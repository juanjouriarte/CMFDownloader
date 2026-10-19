from __future__ import annotations

import time
import random
from calendar import monthrange
from datetime import date

from sqlalchemy import select

from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.fondos_inversion import FondoInversion
from src.http import make_session
from src.etl.investmentFunds.loaders.valores_cuota import parse_html, load_valores_cuota
from src.etl.investmentFunds.loaders.utils import mark_has_data

BASE_URL  = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
BACKFILL_START = date(2020, 1, 1)


def _incremental_start(today: date) -> date:
    """Three calendar months, clamped for shorter destination months."""
    month = today.year * 12 + today.month - 1 - 3
    year, month0 = divmod(month, 12)
    return date(year, month0 + 1, min(today.day, monthrange(year, month0 + 1)[1]))


def _tipo(rescatable: bool) -> str:
    return "FIRES" if rescatable else "FINRE"


def _vig(vigente: bool) -> str:
    return "VI" if vigente else "NV"


def _post_data(start: date, end: date) -> str:
    return (
        f"dia1={start.day:02d}&mes1={start.month:02d}&anio1={start.year}"
        f"&dia2={end.day:02d}&mes2={end.month:02d}&anio2={end.year}"
        "&enviado=1"
    )


class ValoresCuotaFIDownloader(BaseDownloader):
    """Descarga valores cuota diarios de todos los Fondos de Inversión desde CMF."""

    def __init__(self, force: bool = False) -> None:
        super().__init__(output_dir=DOWNLOADS_DIR / "valores_cuota_fi", force=force)

    def run(self) -> DownloadResult:
        today = date.today()
        # Revisit the whole window, including late filings and partial imports.
        from_date = _incremental_start(today)
        return self._download_all(from_date, today, only_vigentes=True)

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        self.logger.info("Backfill valores cuota FI desde %s", from_date)
        return self._download_all(from_date, date.today(), only_vigentes=False)

    def _download_all(self, from_date: date, to_date: date, only_vigentes: bool) -> DownloadResult:
        funds = self._get_funds(only_vigentes)
        total = DownloadResult()

        for i, fund in enumerate(funds, 1):
            run_fondo = fund.run_fondo
            rescatable = fund.rescatable if fund.rescatable is not None else True
            vigente = fund.vigente if fund.vigente is not None else True

            # MAX(fecha) does not prove earlier dates/series are complete. This
            # also lets backfill repair holes behind the latest observation.
            if from_date > to_date:
                total += DownloadResult(skipped=1)
                continue

            self.logger.debug("[%d/%d] %s (%s)", i, len(funds), run_fondo, fund.razon_social or "")

            try:
                records = self._fetch(run_fondo, rescatable, vigente, from_date, to_date)
                rows = load_valores_cuota(records)
                if rows:
                    mark_has_data(run_fondo, True)
                elif not vigente and from_date <= BACKFILL_START:
                    mark_has_data(run_fondo, False)
                total += DownloadResult(downloaded=1, rows_upserted=rows)
            except Exception as exc:
                self.logger.warning("Error en %s: %s", run_fondo, exc)
                total += DownloadResult(errors=1)

            if i % 50 == 0:
                self.logger.info("[%d/%d] %s", i, len(funds), total)

            time.sleep(random.uniform(0.5, 1.2))

        return total

    def _fetch(self, run_fondo: str, rescatable: bool, vigente: bool,
               start: date, end: date) -> list[dict]:
        session = make_session(headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": BASE_URL,
        })
        url = (
            f"{BASE_URL}?mercado=V&rut={run_fondo}&grupo=&tipoentidad={_tipo(rescatable)}"
            f"&vig={_vig(vigente)}&control=svs&pestania=7"
        )
        resp = session.post(url, data=_post_data(start, end), timeout=30)
        resp.raise_for_status()
        return parse_html(resp.text, run_fondo)

    def _get_funds(self, only_vigentes: bool) -> list[FondoInversion]:
        with SessionLocal() as session:
            q = select(FondoInversion).where(FondoInversion.has_data.is_not(False))
            if only_vigentes:
                q = q.where(FondoInversion.vigente == True)
            funds = session.execute(q).scalars().all()
            for f in funds:
                session.expunge(f)
            return funds
