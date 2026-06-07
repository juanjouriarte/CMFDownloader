from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.aportantes_fi import AportanteFI, CuotasFI

logger = logging.getLogger(__name__)

_CUOTA_KEYS = {
    "2.01.70": "cuotas_emitidas",
    "2.01.71": "cuotas_pagadas",
    "2.01.72": "cuotas_suscritas_no_pagadas",
    "2.01.73": "num_cuotas_promesa",
    "2.01.74": "num_contratos_promesa",
    "2.01.75": "num_promitentes",
    "2.01.80": "valor_libro",
}


def _to_decimal(s: str) -> Decimal | None:
    if not s or s.strip() in ("-", ""):
        return None
    s = s.strip()
    try:
        if "," in s:
            # Chilean format: period=thousands, comma=decimal  e.g. "1.234,56"
            return Decimal(s.replace(".", "").replace(",", "."))
        elif s.count(".") > 1:
            # Multiple periods = thousands separators, no decimal  e.g. "26.613.189"
            return Decimal(s.replace(".", ""))
        else:
            # Standard decimal or integer  e.g. "87.0604", ".6394", "4667704"
            return Decimal(s)
    except InvalidOperation:
        return None


def _parse_rut(s: str) -> tuple[str | None, str | None]:
    """'84177300 - 4' → ('84177300', '4')"""
    parts = s.replace(" ", "").split("-")
    return (parts[0] or None, parts[1] if len(parts) > 1 else None)


def parse_html(html: str, run_fondo: str, periodo: date) -> tuple[list[dict], dict | None]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    aportantes: list[dict] = []
    cuotas: dict | None = None

    if len(tables) >= 1:
        for row in tables[0].find_all("tr")[1:]:  # skip header
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 5 or not cells[0].isdigit():
                continue
            rut, dv = _parse_rut(cells[3])
            aportantes.append({
                "run_fondo":    run_fondo,
                "periodo":      periodo,
                "rank":         int(cells[0]),
                "nombre":       cells[1] or None,
                "tipo_persona": cells[2] or None,
                "rut":          rut,
                "dv_rut":       dv,
                "pct_propiedad": _to_decimal(cells[4]),
            })

    if len(tables) >= 2:
        cuotas = {"run_fondo": run_fondo, "periodo": periodo}
        for row in tables[1].find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 2:
                continue
            for code, field in _CUOTA_KEYS.items():
                if cells[0].startswith(code):
                    cuotas[field] = _to_decimal(cells[1])
                    break

    return aportantes, cuotas


def load_aportantes(aportantes: list[dict], cuotas: dict | None) -> int:
    total = 0
    with SessionLocal() as session:
        if aportantes:
            stmt = insert(AportanteFI).values(aportantes)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_aportante_fi",
                set_={c: stmt.excluded[c] for c in aportantes[0]
                      if c not in ("run_fondo", "periodo", "rank")},
            )
            session.execute(stmt)
            total += len(aportantes)

        if cuotas and len(cuotas) > 2:
            stmt = insert(CuotasFI).values([cuotas])
            stmt = stmt.on_conflict_do_update(
                index_elements=["run_fondo", "periodo"],
                set_={c: stmt.excluded[c] for c in cuotas
                      if c not in ("run_fondo", "periodo")},
            )
            session.execute(stmt)
            total += 1

        session.commit()
    return total
