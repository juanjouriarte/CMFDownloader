from __future__ import annotations

import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import SessionLocal
from src.db.models.ref_codes import RefCode

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CMFDownloader/1.0)"}

_SOURCES = {
    "country":    "https://www.cmfchile.cl/institucional/seil/certificacion_paises.php",
    "currency":   "https://www.cmfchile.cl/institucional/seil/certificacion_monedas.php",
    "instrument": "https://www.cmfchile.cl/institucional/seil/certificacion_inst.php",
}


def _scrape(domain: str, url: str, now: datetime) -> list[dict]:
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError(f"Table not found on CMF page: {url}")

    records: list[dict] = []
    seen: set[str] = set()
    for table in tables:
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) != 2:
                continue  # section headers (1 cell) or malformed rows
            name, code = cells
            if not code or code.lower() in ("código", "codigo", "code"):
                continue  # column header row
            if code in seen:
                continue  # deduplicate codes that appear in multiple tables
            seen.add(code)
            records.append({"domain": domain, "code": code, "name": name, "updated_at": now})

    if not records:
        raise ValueError(f"No records parsed for domain '{domain}' from {url}")
    return records


def run() -> int:
    """Scrape CMF reference pages for countries, currencies, and instruments. Returns total rows upserted."""
    now = datetime.utcnow()
    all_records: list[dict] = []
    for domain, url in _SOURCES.items():
        records = _scrape(domain, url, now)
        logger.info("ref_codes[%s]: %d records scraped", domain, len(records))
        all_records.extend(records)

    stmt = pg_insert(RefCode).values(all_records)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ref_codes_domain_code",
        set_={"name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()

    logger.info("ref_codes: %d total rows upserted", len(all_records))
    return len(all_records)
