from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.mutual_funds import FondoMutuo

logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "RUN_FONDO": "run_fondo",
    "NOMBRE_FONDO": "nombre_fondo",
    "RUN_SOCIEDAD": "run_sociedad",
    "NOMBRE_SOCIEDAD": "nombre_sociedad",
    "TIPO_FONDO": "tipo_fondo",
    "MONEDA": "moneda",
    "FECHA_INICIO": "fecha_inicio",
}


def load_identidad(path: Path) -> int:
    df = pd.read_csv(path, sep="|", dtype=str, encoding="latin-1")
    df.columns = df.columns.str.strip()

    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    df = df.where(pd.notna(df), None)

    if "fecha_inicio" in df.columns:
        df["fecha_inicio"] = pd.to_datetime(df["fecha_inicio"], errors="coerce").dt.date

    records = df.to_dict(orient="records")

    with SessionLocal() as session:
        stmt = insert(FondoMutuo).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["run_fondo"],
            set_={c: stmt.excluded[c] for c in records[0] if c != "run_fondo"},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Identidad FM: %d filas insertadas/actualizadas", len(records))
    return len(records)
