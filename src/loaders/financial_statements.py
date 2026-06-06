from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.financial_statements import FinancialStatement

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000


def _fix_mojibake(s: str) -> str:
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _parse_periodo(yyyymm: str) -> date:
    return date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)


def load_financial_statements(path: Path) -> int:
    content = path.read_bytes().decode("latin-1")
    lines = content.strip().split("\n")

    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line or "no se encuentra" in line.lower():
            continue
        parts = line.split(";")
        if len(parts) < 9:
            continue
        try:
            records.append({
                "periodo":            _parse_periodo(parts[0].strip()),
                "rut":                parts[1].strip(),
                "company_name":       _fix_mojibake(parts[2].strip()),
                "consolidation_type": parts[3].strip() or None,
                "currency":           parts[4].strip() or None,
                "account":            _fix_mojibake(parts[5].strip()),
                "value":              int(parts[6].strip()) if parts[6].strip() else None,
                "tax_type":           parts[7].strip() or None,
                "statement_type":     parts[8].strip() or None,
            })
        except (ValueError, IndexError):
            continue

    if not records:
        logger.warning("No records parsed from %s", path.name)
        return 0

    # Delete existing data for all periods in this file then re-insert
    periods = list({r["periodo"] for r in records})
    with SessionLocal() as session:
        for periodo in periods:
            session.execute(delete(FinancialStatement).where(FinancialStatement.periodo == periodo))
        for start in range(0, len(records), BATCH_SIZE):
            session.execute(insert(FinancialStatement).values(records[start: start + BATCH_SIZE]))
        session.commit()

    logger.info("Financial statements: %d rows loaded (%d periods)", len(records), len(periods))
    return len(records)
