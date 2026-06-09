from __future__ import annotations

from pathlib import Path

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.http import make_session
from src.etl.investmentFunds.loaders.nemotecnicos import load_nemotecnicos_fi

OUTPUT_FILE = "fi_nemotecnicos.html"


class FINemotecnicosDownloader(BaseDownloader):
    """Descarga e ingesta los códigos nemotécnicos de Fondos de Inversión desde CMF."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "nemotecnicos_fi", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        path = self.output_dir / OUTPUT_FILE

        if self._should_skip(path):
            self.logger.info("fi_nemotecnicos.html ya existe — usa force=True para re-descargar")
            rows = load_nemotecnicos_fi(path)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session(headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.cmfchile.cl/institucional/seil/",
        })

        resp = session.get(CMFUrl.FI_NEMOTECNICOS, timeout=30)
        resp.raise_for_status()

        if not resp.content:
            self.logger.error("Respuesta vacía del servidor")
            return DownloadResult(errors=1)

        path.write_bytes(resp.content)
        self.logger.info("Nemotecnicos FI: %.0f KB → %s", len(resp.content) / 1024, path)

        rows = load_nemotecnicos_fi(path)
        path.unlink(missing_ok=True)
        return DownloadResult(downloaded=1, rows_upserted=rows)
