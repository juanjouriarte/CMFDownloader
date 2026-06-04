from __future__ import annotations

import random
import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from src import captcha
from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.db.models.cartola import CartolaDiaria
from src.http import make_session
from src.mutualFunds.cartolaLoader import load_cartola

MAX_RETRIES = 7
MIN_FILE_BYTES = 200
BACKFILL_START = date(2019, 12, 31)


def _year_ranges(from_date: date, to_date: date) -> list[tuple[date, date]]:
    """Split a date range into yearly chunks."""
    ranges: list[tuple[date, date]] = []
    current = from_date

    while current <= to_date:
        year_end = date(current.year, 12, 31)
        end = min(year_end, to_date)
        ranges.append((current, end))
        current = date(current.year + 1, 1, 1)

    return ranges


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


class CartolaDownloader(BaseDownloader):
    """Descarga e ingesta la cartola diaria de Fondos Mutuos desde CMF."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "cartola", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        """Incremental: downloads from the last date in DB to today."""
        from_date = self._last_date_in_db() or BACKFILL_START
        to_date = date.today()

        if from_date >= to_date:
            self.logger.info("Cartola already up to date (last: %s)", from_date)
            return DownloadResult(skipped=1)

        return self._download_ranges(_year_ranges(from_date, to_date))

    def backfill(self, from_date: date = BACKFILL_START) -> DownloadResult:
        """Historic population: downloads yearly from from_date to today."""
        self.logger.info("Backfill cartola desde %s", from_date)
        return self._download_ranges(_year_ranges(from_date, date.today()))

    def _download_ranges(self, ranges: list[tuple[date, date]]) -> DownloadResult:
        total = DownloadResult()
        for i, (start, end) in enumerate(ranges, 1):
            self.logger.info("[%d/%d] Descargando %s → %s", i, len(ranges), _fmt(start), _fmt(end))
            path = self._fetch(start, end)
            if path is None:
                total += DownloadResult(errors=1)
                continue
            rows = load_cartola(path)
            total += DownloadResult(downloaded=1, rows_upserted=rows)
            if i < len(ranges):
                time.sleep(2)
        return total

    def _fetch(self, start: date, end: date) -> Path | None:
        filename = f"cartola_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.txt"
        dest = self.output_dir / filename

        if self._should_skip(dest) and dest.stat().st_size > MIN_FILE_BYTES:
            self.logger.info("Ya existe: %s — saltando descarga", filename)
            return dest

        session = make_session(headers={"Referer": CMFUrl.CARTOLA_PAGE})
        session.get(CMFUrl.CARTOLA_PAGE, timeout=15)

        for attempt in range(1, MAX_RETRIES + 1):
            self.logger.debug("Intento %d/%d: obteniendo CAPTCHA...", attempt, MAX_RETRIES)

            captcha_resp = session.get(
                CMFUrl.CARTOLA_CAPTCHA,
                params={"rand": random.randint(0, 32767)},
                timeout=10,
            )
            captcha_resp.raise_for_status()

            answer = captcha.solve(captcha_resp.content)
            self.logger.debug("CAPTCHA resuelto: '%s'", answer)

            if not answer:
                self.logger.warning("CAPTCHA vacío en intento %d — reintentando", attempt)
                continue

            resp = session.post(
                CMFUrl.CARTOLA_POST,
                data={
                    "txt_inicio": _fmt(start),
                    "txt_termino": _fmt(end),
                    "ffmm": "%",
                    "captcha": answer,
                },
                timeout=120,
            )

            if resp.status_code == 200 and "text/html" not in resp.headers.get("Content-Type", ""):
                dest.write_bytes(resp.content)
                if dest.stat().st_size < MIN_FILE_BYTES:
                    self.logger.warning("Archivo muy pequeño (%d B) — reintentando", dest.stat().st_size)
                    dest.unlink(missing_ok=True)
                    continue
                self.logger.info("Descargado: %s (%.0f KB)", filename, dest.stat().st_size / 1024)
                return dest

            self.logger.warning("Respuesta HTML en intento %d (CAPTCHA incorrecto?)", attempt)
            time.sleep(1)

        self.logger.error("Fallaron todos los intentos para %s → %s", _fmt(start), _fmt(end))
        return None

    def _last_date_in_db(self) -> date | None:
        with SessionLocal() as session:
            return session.execute(select(func.max(CartolaDiaria.fecha))).scalar_one_or_none()
