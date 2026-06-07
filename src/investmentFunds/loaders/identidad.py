from __future__ import annotations

import logging

from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.fondos_inversion import FondoInversion

logger = logging.getLogger(__name__)


def _parse_html(html: str, rescatable: bool, vigente: bool) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    records = []
    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        rut = cells[0]
        if not rut or "-" not in rut:
            continue
        parts = rut.split("-")
        if not parts[0].strip().isdigit():
            continue
        records.append({
            "run_fondo":    parts[0].strip(),
            "dv_fondo":     parts[1].strip() if len(parts) > 1 else None,
            "razon_social": cells[1] or None,
            "administrador": cells[2] or None,
            "rescatable":   rescatable,
            "vigente":      vigente,
        })

    return records


def load_identidad(pages: list[tuple[str, bool, bool]]) -> int:
    """
    pages: list of (html, rescatable, vigente) — one per fetched URL.
    Upserts all records into fondos_inversion.
    """
    all_records: dict[str, dict] = {}
    for html, rescatable, vigente in pages:
        for r in _parse_html(html, rescatable, vigente):
            all_records[r["run_fondo"]] = r

    rows = list(all_records.values())
    if not rows:
        logger.warning("Identidad FI: sin registros")
        return 0

    with SessionLocal() as session:
        stmt = insert(FondoInversion).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["run_fondo"],
            set_={c: stmt.excluded[c] for c in rows[0] if c != "run_fondo"},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Fondos inversión: %d fondos upserted", len(rows))
    return len(rows)
