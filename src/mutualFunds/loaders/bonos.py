from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.bonos import BonoNemotecnico

logger = logging.getLogger(__name__)

_DATE_COLS = ["fecha_ingreso", "fecha_inscripcion", "fecha_colocacion"]


def _parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("No table found in HTML")

    records = []
    for row in table.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) != 8:
            continue
        records.append({
            "fecha_ingreso":      cells[0],
            "nombre_emisor":      cells[1],
            "rut_emisor":         cells[2],
            "numero_inscripcion": cells[3],
            "fecha_inscripcion":  cells[4],
            "fecha_colocacion":   cells[5],
            "nemotecnico":        cells[6],
            "tasa_fiscal":        cells[7],
        })

    if not records:
        raise ValueError("No data rows found in table")

    # Source HTML has duplicate rows — keep last occurrence
    records = list({r["nemotecnico"]: r for r in records}.values())

    df = pd.DataFrame(records)

    for col in _DATE_COLS:
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce").dt.date
        df[col] = df[col].where(df[col].notna(), None)

    df["tasa_fiscal"] = pd.to_numeric(
        df["tasa_fiscal"].str.replace(",", ".", regex=False), errors="coerce"
    )
    df["tasa_fiscal"] = df["tasa_fiscal"].where(df["tasa_fiscal"].notna(), None)
    df = df.where(pd.notna(df), None)

    return df.to_dict(orient="records")


def load_bonos(path: Path) -> int:
    records = _parse_html(path.read_text(encoding="utf-8"))

    with SessionLocal() as session:
        stmt = insert(BonoNemotecnico).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["nemotecnico"],
            set_={c: stmt.excluded[c] for c in records[0] if c != "nemotecnico"},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Bonos nemotecnicos: %d instrumentos upserted", len(records))
    return len(records)
