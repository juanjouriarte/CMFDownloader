from __future__ import annotations

from pathlib import Path

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.http import make_session
from src.mutualFunds.loaders.bonos import load_bonos

OUTPUT_FILE = "bonos_nemotecnicos.html"


class BonosNemotecnicosDownloader(BaseDownloader):
    """Descarga e ingesta los bonos con tasa de interés fiscal desde CMF."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "bonos", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        path = self.output_dir / OUTPUT_FILE

        if self._should_skip(path):
            self.logger.info("bonos_nemotecnicos.html ya existe — usa force=True para re-descargar")
            rows = load_bonos(path)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session(headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.cmfchile.cl/institucional/estadisticas/",
        })

        resp = session.get(CMFUrl.BONOS_NEMOTECNICOS, timeout=30)
        resp.raise_for_status()

        if not resp.content:
            self.logger.error("Respuesta vacía del servidor")
            return DownloadResult(errors=1)

        path.write_bytes(resp.content)
        self.logger.info("Bonos nemotecnicos: %.0f KB → %s", len(resp.content) / 1024, path)

        rows = load_bonos(path)
        return DownloadResult(downloaded=1, rows_upserted=rows)
