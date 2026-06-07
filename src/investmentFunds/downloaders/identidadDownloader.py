from __future__ import annotations

from pathlib import Path

from src.base import BaseDownloader, DownloadResult
from src.config import CMFUrl, DOWNLOADS_DIR
from src.http import make_session
from src.investmentFunds.loaders.identidad import load_identidad

# (tipo, estado, rescatable, vigente)
_SOURCES = [
    ("FIRES", "VI",  True,  True),
    ("FIRES", "NV",  True,  False),
    ("FINRE", "VI",  False, True),
    ("FINRE", "NV",  False, False),
]


class FIIdentidadDownloader(BaseDownloader):
    """Descarga el registro completo de Fondos de Inversión desde CMF y lo carga en fondos_inversion."""

    def __init__(self, output_dir: Path = DOWNLOADS_DIR / "identidad_fi", force: bool = False) -> None:
        super().__init__(output_dir, force)

    def run(self) -> DownloadResult:
        session = make_session(headers={"Referer": "https://www.cmfchile.cl/institucional/mercados/"})
        pages: list[tuple[str, bool, bool]] = []

        for tipo, estado, rescatable, vigente in _SOURCES:
            url = CMFUrl.FI_IDENTIDAD.format(tipo=tipo, estado=estado)
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            self.logger.info("%s %s: %.0f KB", tipo, estado, len(resp.content) / 1024)
            pages.append((resp.text, rescatable, vigente))

        rows = load_identidad(pages)
        return DownloadResult(downloaded=4, rows_upserted=rows)
