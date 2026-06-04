from __future__ import annotations

import logging
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.nemotecnicos import Nemotecnico

logger = logging.getLogger(__name__)

_SKIP_HEADERS = {"rut", "run"}


def _parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("No table found in HTML")

    records = []
    current_admin: dict[str, str | None] = {"rut": None, "dv": None, "nombre": None}

    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) != 6:
            continue
        if cells[0].lower() in _SKIP_HEADERS:
            continue
        if cells[0].isdigit() and cells[3] == "" and cells[4] == "" and cells[5] == "":
            current_admin = {"rut": cells[0], "dv": cells[1], "nombre": " ".join(cells[2].split())}
            continue
        if all(cells):
            records.append({
                "nemotecnico":  cells[5],
                "run_fondo":    cells[0],
                "dv_fondo":     cells[1],
                "razon_social": cells[2],
                "nombre_fondo": cells[3],
                "serie":        cells[4],
                "admin_rut":    current_admin["rut"],
                "admin_dv":     current_admin["dv"],
                "admin_nombre": current_admin["nombre"],
            })

    if not records:
        raise ValueError("No data rows found in table")

    # Source HTML has occasional duplicate nemotecnicos across different series — keep last occurrence
    return list({r["nemotecnico"]: r for r in records}.values())


def load_nemotecnicos(path: Path) -> int:
    records = _parse_html(path.read_text(encoding="utf-8"))

    with SessionLocal() as session:
        stmt = insert(Nemotecnico).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["nemotecnico"],
            set_={c: stmt.excluded[c] for c in records[0] if c != "nemotecnico"},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Nemotecnicos: %d series upserted", len(records))
    return len(records)
