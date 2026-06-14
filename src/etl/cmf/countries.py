from __future__ import annotations

import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import SessionLocal
from src.db.models.countries import Country

logger = logging.getLogger(__name__)

_URL = "https://www.cmfchile.cl/institucional/seil/certificacion_paises.php"


def run() -> int:
    """Fetch CMF country code table and upsert into countries. Returns rows upserted."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CMFDownloader/1.0)"}
    resp = requests.get(_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("Country table not found on CMF page")

    now = datetime.utcnow()
    records: list[dict] = []
    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        name = cells[0].get_text(strip=True)
        code = cells[1].get_text(strip=True)
        if code:
            records.append({"code": code, "name": name, "updated_at": now})

    if not records:
        raise ValueError("No country records parsed from CMF page")

    stmt = pg_insert(Country).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={"name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()

    logger.info("countries: %d rows upserted", len(records))
    return len(records)
