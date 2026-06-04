from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.cartola import CartolaDiaria

logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "RUN_ADM": "run_adm",
    "NOM_ADM": "nom_adm",
    "RUN_FM": "run_fondo",
    "FECHA_INF": "fecha",
    "ACTIVO_TOT": "activo_tot",
    "MONEDA": "moneda",
    "PARTICIPES_INST": "participes_inst",
    "INVERSION_EN_FONDOS": "inversion_en_fondos",
    "SERIE": "serie",
    "CUOTAS_APORTADAS": "cuotas_aportadas",
    "CUOTAS_RESCATADAS": "cuotas_rescatadas",
    "CUOTAS_EN_CIRCULACION": "cuotas_en_circulacion",
    "VALOR_CUOTA": "valor_cuota",
    "PATRIMONIO_NETO": "patrimonio_neto",
    "NUM_PARTICIPES": "num_participes",
    "NUM_PARTICIPES_INST": "num_participes_inst",
    "FONDO_PEN": "fondo_pen",
    "REM_FIJA": "rem_fija",
    "REM_VARIABLE": "rem_variable",
    "GASTOS_AFECTOS": "gastos_afectos",
    "GASTOS_NO_AFECTOS": "gastos_no_afectos",
    "COMISION_INVERSION": "comision_inversion",
    "COMISION_RESCATE": "comision_rescate",
    "FACTOR DE AJUSTE": "factor_ajuste",
    "FACTOR DE REPARTO": "factor_reparto",
}

NUMERIC_COLS = [
    "activo_tot", "inversion_en_fondos", "cuotas_aportadas", "cuotas_rescatadas",
    "cuotas_en_circulacion", "valor_cuota", "patrimonio_neto", "rem_fija",
    "rem_variable", "gastos_afectos", "gastos_no_afectos", "comision_inversion",
    "comision_rescate", "factor_ajuste", "factor_reparto",
]
INT_COLS = ["num_participes", "num_participes_inst"]


def load_cartola(path: Path) -> int:
    df = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8")
    df.columns = df.columns.str.strip()
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    df = df.where(pd.notna(df), None)

    if "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], format="%Y%m%d", errors="coerce").dt.date
        df["fecha"] = df["fecha"].where(df["fecha"].notna(), None)

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].str.replace(",", "."), errors="coerce")
            df[col] = df[col].where(df[col].notna(), None)

    for col in INT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].where(df[col].notna(), None)

    df = df.dropna(subset=["fecha", "run_fondo"])
    records = df.to_dict(orient="records")

    if not records:
        logger.warning("No records to upsert from %s", path.name)
        return 0

    with SessionLocal() as session:
        stmt = insert(CartolaDiaria).values(records)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_cartola_fecha_fondo_serie",
            set_={c: stmt.excluded[c] for c in records[0] if c not in ("fecha", "run_fondo", "serie")},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Cartola %s: %d filas upserted", path.name, len(records))
    return len(records)
