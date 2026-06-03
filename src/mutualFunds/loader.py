from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.mutual_funds import FondoMutuo

logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "RUT Administradora": "rut_administradora",
    "Raz. Social Administradora": "razon_social_administradora",
    "RUN Fondo": "run_fondo",
    "Nombre Fondo": "nombre_fondo",
    "Nombre Corto": "nombre_corto",
    "Tipo de Fondo Mutuo": "tipo_fondo",
    "Moneda": "moneda",
    "Fecha Inicio Operaciones": "fecha_inicio_operaciones",
    "Fecha Término Operaciones": "fecha_termino_operaciones",
    "Fecha Res. Aprobación del RI": "fecha_res_aprobacion",
    "Nro. Res. Aprobación del RI": "nro_res_aprobacion",
}

DATE_COLS = ["fecha_inicio_operaciones", "fecha_termino_operaciones", "fecha_res_aprobacion"]


def load_identidad(path: Path) -> int:
    df = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8")
    df.columns = df.columns.str.strip()
    df = df.rename(columns=COLUMN_MAP)
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    df = df.drop_duplicates(subset=["run_fondo"])
    df = df.where(pd.notna(df), None)

    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce").dt.date

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
