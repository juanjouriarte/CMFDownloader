from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import xlrd
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.tac import Tac

logger = logging.getLogger(__name__)

COLS = [
    "periodo_raw", "administradora", "nombre_fondo", "tipo_fondo", "moneda", "serie",
    "caracteristicas", "rem_fija", "rem_var", "gastos_op",
    "tac_rem_fija", "tac_rem_var", "tac_gastos_op", "tac_total",
    "cond_colocacion", "com_colocacion", "cond_diferida", "com_diferida",
]

TAC_NUMERIC = ["tac_rem_fija", "tac_rem_var", "tac_gastos_op", "tac_total"]


def _fix_mojibake(s: str) -> str:
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _normalize(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper().strip()


def _strip_suffix(s: str) -> str:
    return re.sub(r"\s*\(\d+\)\s*$", "", s).strip()


def _to_numeric(val) -> float | None:
    if not val or str(val).strip().upper() in ("NA", "N/A", ""):
        return None
    try:
        return float(str(val).replace(",", ".").strip())
    except ValueError:
        return None


def _build_run_fondo_lookup() -> dict[tuple[str, str], str]:
    """
    Returns a dict of (nombre_norm, admin_norm) → run_fondo.
    For duplicate fund names under the same admin, picks the run_fondo
    with the most recent cartola_diaria date (active fund).
    """
    with SessionLocal() as s:
        fm = s.execute(text(
            "SELECT run_fondo, nombre_fondo, razon_social_administradora FROM fondo_mutuo"
        )).fetchall()
        latest = dict(s.execute(text(
            "SELECT run_fondo, MAX(fecha) FROM cartola_diaria GROUP BY run_fondo"
        )).fetchall())

    lookup: dict[tuple[str, str], tuple[str, date | None]] = {}
    for run_fondo, nombre_fondo, razon_social in fm:
        nombre_norm = _normalize(_fix_mojibake(nombre_fondo or ""))
        admin_norm  = _normalize(_fix_mojibake(razon_social or ""))
        key = (nombre_norm, admin_norm)
        fecha = latest.get(run_fondo)
        existing = lookup.get(key)
        if existing is None or (fecha and (existing[1] is None or fecha > existing[1])):
            lookup[key] = (run_fondo, fecha)

    return {k: v[0] for k, v in lookup.items()}


def load_tac(path: Path, year: int, month: int) -> int:
    periodo = date(year, month, 1)

    book = xlrd.open_workbook(str(path), encoding_override="latin-1")
    sheet = book.sheet_by_index(0)

    rows = []
    for r in range(4, sheet.nrows):
        val = sheet.cell_value(r, 0)
        try:
            int(float(val))  # valid date like 20260531.0
            rows.append([sheet.cell_value(r, c) for c in range(18)])
        except (TypeError, ValueError):
            pass

    if not rows:
        logger.warning("No data rows in %s", path.name)
        return 0

    df = pd.DataFrame(rows, columns=COLS)
    df["administradora"] = df["administradora"].apply(_fix_mojibake)
    df["nombre_fondo"]   = df["nombre_fondo"].apply(_fix_mojibake)
    df["nombre_norm"]    = df["nombre_fondo"].apply(_normalize)
    df["admin_norm"]     = df["administradora"].apply(_normalize)
    df["nombre_no_suffix"] = df["nombre_norm"].apply(_strip_suffix)

    # Parse periodo from cell (e.g. 20260531.0 → date)
    def parse_periodo(v) -> date:
        s = str(int(float(v)))
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    df["periodo"] = df["periodo_raw"].apply(parse_periodo)

    # TAC numeric columns
    for col in TAC_NUMERIC:
        df[col] = df[col].apply(_to_numeric)

    # Clean text nulls
    text_cols = ["rem_fija", "rem_var", "gastos_op", "cond_colocacion",
                 "com_colocacion", "cond_diferida", "com_diferida", "caracteristicas"]
    for col in text_cols:
        df[col] = df[col].apply(
            lambda v: None if not v or str(v).strip().upper() in ("NA", "N/A", "") else str(v).strip()
        )

    df["tipo_fondo"] = df["tipo_fondo"].apply(lambda v: str(int(float(v))) if v else None)
    df["serie"]      = df["serie"].apply(lambda v: str(v).strip() if v else None)
    df["moneda"]     = df["moneda"].apply(lambda v: str(v).strip() if v else None)

    # Match run_fondo
    lookup = _build_run_fondo_lookup()
    def match(row) -> str | None:
        key = (row["nombre_norm"], row["admin_norm"])
        if key in lookup:
            return lookup[key]
        key2 = (row["nombre_no_suffix"], row["admin_norm"])
        return lookup.get(key2)

    df["run_fondo"] = df.apply(match, axis=1)

    unmatched = df["run_fondo"].isna().sum()
    if unmatched:
        logger.warning("%d rows could not be matched to run_fondo", unmatched)

    records = df[[
        "periodo", "run_fondo", "administradora", "nombre_fondo", "tipo_fondo",
        "moneda", "serie", "caracteristicas", "rem_fija", "rem_var", "gastos_op",
        "tac_rem_fija", "tac_rem_var", "tac_gastos_op", "tac_total",
        "cond_colocacion", "com_colocacion", "cond_diferida", "com_diferida",
    ]].where(pd.notna(df), None).to_dict(orient="records")

    with SessionLocal() as session:
        session.execute(delete(Tac).where(Tac.periodo == periodo))
        for start in range(0, len(records), 5000):
            session.execute(insert(Tac).values(records[start: start + 5000]))
        session.commit()

    logger.info("TAC %d-%02d: %d filas cargadas (%d sin run_fondo)", year, month, len(records), unmatched)
    return len(records)
