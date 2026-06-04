from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.cartola import CartolaDiaria

logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "RUT Administradora": "rut_administradora",
    "Nombre Administradora": "nombre_administradora",
    "RUN Fondo": "run_fondo",
    "Nombre Fondo": "nombre_fondo",
    "Fecha": "fecha",
    "Valor Cuota": "valor_cuota",
    "Patrimonio": "patrimonio",
    "Numero de Cuotas": "numero_cuotas",
    "Participes": "participes",
}

NUMERIC_COLS = ["valor_cuota", "patrimonio", "numero_cuotas"]


def load_cartola(path: Path) -> int:
    df = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8")
    df.columns = df.columns.str.strip()
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    df = df.where(pd.notna(df), None)

    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce").dt.date
        df["fecha"] = df["fecha"].where(df["fecha"].notna(), None)

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].str.replace(",", "."), errors="coerce")
            df[col] = df[col].where(df[col].notna(), None)

    if "participes" in df.columns:
        df["participes"] = pd.to_numeric(df["participes"], errors="coerce")
        df["participes"] = df["participes"].where(df["participes"].notna(), None)
        df["participes"] = df["participes"].astype("Int64")

    df = df.dropna(subset=["fecha", "run_fondo"])
    records = df.to_dict(orient="records")

    if not records:
        logger.warning("No records to upsert from %s", path.name)
        return 0

    with SessionLocal() as session:
        stmt = insert(CartolaDiaria).values(records)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_cartola_fecha_fondo",
            set_={c: stmt.excluded[c] for c in records[0] if c not in ("fecha", "run_fondo")},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Cartola %s: %d filas upserted", path.name, len(records))
    return len(records)
