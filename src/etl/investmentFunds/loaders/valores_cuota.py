from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.valores_cuota_fi import ValorCuotaFI

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000

_FLUJO_NETO_SQL = """
    WITH lagged AS (
        SELECT run_fondo, serie, fecha,
            (
                patrimonio_neto::numeric / NULLIF(valor_libro, 0)
                - LAG(patrimonio_neto::numeric / NULLIF(valor_libro, 0)) OVER w
            ) * valor_libro AS flujo
        FROM valores_cuota_fi
        WHERE run_fondo = :run_fondo
          AND fecha >= :lookback
          AND valor_libro IS NOT NULL AND valor_libro > 0
          AND patrimonio_neto IS NOT NULL
        WINDOW w AS (PARTITION BY run_fondo, serie ORDER BY fecha)
    )
    UPDATE valores_cuota_fi v
    SET flujo_neto = l.flujo
    FROM lagged l
    WHERE v.run_fondo = l.run_fondo
      AND v.serie IS NOT DISTINCT FROM l.serie
      AND v.fecha = l.fecha
      AND v.fecha >= :from_date
      AND l.flujo IS NOT NULL
"""


def _parse_date(s: str):
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def _to_decimal(s: str) -> Decimal | None:
    if not s or s.strip() == "-":
        return None
    try:
        return Decimal(s.strip().replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _to_int(s: str) -> int | None:
    if not s or s.strip() == "-":
        return None
    try:
        return int(s.strip().replace(".", "").replace(",", ""))
    except ValueError:
        return None


def parse_html(html: str, run_fondo: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    rows = table.find_all("tr")
    records = []
    for row in rows[1:]:  # skip header
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 8:
            continue
        try:
            records.append({
                "run_fondo":           run_fondo,
                "fecha":               _parse_date(cells[0]),
                "serie":               cells[1] or None,
                "moneda":              cells[2] or None,
                "valor_libro":         _to_decimal(cells[3]),
                "valor_economico":     _to_decimal(cells[4]),
                "patrimonio_neto":     _to_int(cells[5]),
                "activo_total":        _to_int(cells[6]),
                "num_aportantes":      _to_int(cells[7]),
                "num_aportantes_inst": _to_int(cells[8]) if len(cells) > 8 else None,
                "agencia":             cells[9] if len(cells) > 9 else None,
            })
        except (ValueError, IndexError):
            continue

    return records


def load_valores_cuota(records: list[dict]) -> int:
    if not records:
        return 0

    run_fondo = records[0]["run_fondo"]
    min_fecha = min(r["fecha"] for r in records if r.get("fecha"))

    with SessionLocal() as session:
        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start:start + BATCH_SIZE]
            stmt = insert(ValorCuotaFI).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_valor_cuota_fi",
                set_={c: stmt.excluded[c] for c in batch[0]
                      if c not in ("run_fondo", "fecha", "serie")},
            )
            session.execute(stmt)

        # Recompute flujo_neto for newly loaded rows.
        # lookback = 3 days before min_fecha to provide LAG() values across weekends.
        session.execute(text(_FLUJO_NETO_SQL), {
            "run_fondo": run_fondo,
            "from_date": min_fecha,
            "lookback":  min_fecha - timedelta(days=3),
        })
        session.commit()

    return len(records)
