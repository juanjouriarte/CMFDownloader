from __future__ import annotations

from pathlib import Path

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.http import make_session
from src.mutualFunds.loaders.identidad import load_identidad

OUTPUT_FILE = "fm_identidad.txt"


class FMIdentidadDownloader(BaseDownloader):
    """Descarga e ingesta el registro de identificación de Fondos Mutuos desde CMF."""

    def __init__(
        self,
        output_dir: Path = DOWNLOADS_DIR / "identidad",
        force: bool = False,
    ) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        path = self.output_dir / OUTPUT_FILE

        if self._should_skip(path):
            self.logger.info("fm_identidad.txt ya existe — usa --force para re-descargar")
            # Still run the load in case the DB is out of sync
            rows = load_identidad(path)
            return DownloadResult(skipped=1, rows_upserted=rows)

        session = make_session(
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.cmfchile.cl/institucional/estadisticas/fm_ident1.php",
            }
        )

        resp = session.post(CMFUrl.FM_IDENTIDAD, data="", timeout=30)
        resp.raise_for_status()

        if not resp.content or "html" in resp.headers.get("Content-Type", ""):
            self.logger.error("Sin datos de identidad FM")
            return DownloadResult(errors=1)

        path.write_bytes(resp.content)
        self.logger.info("Identidad FM: %.0f KB → %s", len(resp.content) / 1024, path)

        rows = load_identidad(path)
        return DownloadResult(downloaded=1, rows_upserted=rows)
