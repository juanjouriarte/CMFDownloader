from __future__ import annotations

import logging
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.nemotecnicos import Nemotecnico

logger = logging.getLogger(__name__)

_SKIP_HEADERS = {"rut", "run"}

# Classification rules applied in order — first match wins
_RULES: list[tuple[str, list[str]]] = [
    ("APV", [
        "APV", "AHORRO PREVISIONAL VOLUNTARIO", "D.L. 3.500", "D.L.3.500",
        "FINES DE APV", "PLANES DE APV", "PLANES DE AHORRO PREVISIONAL",
        "CONSTITUIR PLAN DE AHORRO PREVISIONAL", "CALIDAD DE AHORRO PREVISIONAL",
        "OBJETO DE INVERSION DE LOS PLANES DE APV",
    ]),
    ("AFP", [
        "ADMINISTRADORAS DE FONDOS DE PENSIONES", "FONDOS DE PENSIONES",
        "APORTES AFP", "AFP O FONDOS",
    ]),
    ("Institucional", [
        "INVERSIONISTAS INSTITUCIONALES", "APORTES INSTITUCIONALES",
        "APORTE EFECTUADO POR INVERSIONISTAS INSTITUCIONALES",
        "COMPAÑIAS DE SEGUROS", "COMPANIAS DE SEGUROS", "SEGUROS DE VIDA",
        "CORREDORES DE BOLSA", "FONDOS DE CESANTIA",
    ]),
    ("Fondos", [
        "OTROS FONDOS ADMINISTRADOS POR LA ADMINISTRADORA",
        "OTRO FONDO ADMINISTRADO POR LA ADMINISTRADORA",
        "INVERSIONES DE LA ADMINISTRADORA U OTROS FONDOS",
        "INVERSIONES DE OTROS FONDOS ADMINISTRADOS",
        "CARTERA ADMINISTRADA", "CARTERAS ADMINISTRADAS",
        "ADM DE CARTERA", "APORTES ADM", "APORTES SAM",
        "APORTE ADM DE CARTERA", "APORTE DEBE SER REALIZADO POR ESTE U OTRO FONDO",
    ]),
    ("Digital", [
        "TYBA", "TENPO", "MACH", "MEDIOS ELECTRONICOS",
        "MEDIOS REMOTOS INTERNET", "APLICACIONES DIGITALES",
        "CANAL DIGITAL", "CANALES DIGITALES", "INTERNET",
    ]),
    ("Empleados", [
        "EMPLEADOS DE BANCO BCI", "EMPLEADOS DE LA ADMINISTRADORA",
        "COLABORADORES DEL GRUPO", "EMPLEADOS DE LA ADMNISTRADORA",
    ]),
    ("General", [
        "APORTES GENERALES", "SIN MONTO MINIMO", "TODO TIPO DE INVERSIONISTA",
        "TODO TIPO DE CLIENTES", "PERSONAS NATURALES",
        "FINES DISTINTOS DE APV", "FINES DISTINTOS DE AHORRO PREVISIONAL",
        "DISTINTOS DEL AHORRO PREVISIONAL",
    ]),
]


def classify_serie(serie: str, caracteristicas: str | None) -> str | None:
    """Classify a fund series based on its serie name and TAC caracteristicas text."""
    text_upper = (caracteristicas or "").upper()
    serie_upper = (serie or "").upper()

    # Serie name takes priority for APV
    if "APV" in serie_upper:
        return "APV"

    for tipo, keywords in _RULES:
        if any(kw in text_upper for kw in keywords):
            return tipo

    return None


def enrich_tipo_serie_from_tac() -> int:
    """
    Updates nemotecnicos.tipo_serie using the latest TAC caracteristicas for each
    (run_fondo, serie) combination. Only updates rows where the classification
    improves on the current value (NULL → something).
    """
    with SessionLocal() as session:
        # Get latest caracteristicas per (run_fondo, serie) from TAC
        rows = session.execute(text("""
            SELECT DISTINCT ON (run_fondo, serie)
                run_fondo, serie, caracteristicas
            FROM tac
            WHERE run_fondo IS NOT NULL AND serie IS NOT NULL
            ORDER BY run_fondo, serie, periodo DESC
        """)).fetchall()

        updated = 0
        for run_fondo, serie, caract in rows:
            tipo = classify_serie(serie, caract)
            if tipo:
                result = session.execute(text("""
                    UPDATE nemotecnicos
                    SET tipo_serie = :tipo
                    WHERE run_fondo = :run AND serie = :serie
                """), {"tipo": tipo, "run": run_fondo, "serie": serie})
                updated += result.rowcount

        session.commit()

    logger.info("tipo_serie enriched from TAC: %d series updated", updated)
    return updated


def _parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("No table found in HTML")

    records = []
    current_admin: dict[str, str | None] = {"rut": None, "dv": None, "nombre": None}

    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) != 6:
            continue
        if cells[0].lower() in _SKIP_HEADERS:
            continue
        if cells[0].isdigit() and cells[3] == "" and cells[4] == "" and cells[5] == "":
            current_admin = {"rut": cells[0], "dv": cells[1], "nombre": " ".join(cells[2].split())}
            continue
        if all(cells):
            serie = cells[4]
            records.append({
                "nemotecnico":  cells[5],
                "run_fondo":    cells[0],
                "dv_fondo":     cells[1],
                "razon_social": cells[2],
                "nombre_fondo": cells[3],
                "serie":        serie,
                "tipo_serie":   "APV" if "APV" in serie.upper() else None,
                "admin_rut":    current_admin["rut"],
                "admin_dv":     current_admin["dv"],
                "admin_nombre": current_admin["nombre"],
            })

    if not records:
        raise ValueError("No data rows found in table")

    # Source HTML has occasional duplicate nemotecnicos across different series — keep last occurrence
    return list({r["nemotecnico"]: r for r in records}.values())


def load_nemotecnicos(path: Path) -> int:
    records = _parse_html(path.read_text(encoding="utf-8"))

    with SessionLocal() as session:
        stmt = insert(Nemotecnico).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["nemotecnico"],
            set_={c: stmt.excluded[c] for c in records[0] if c != "nemotecnico"},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Nemotecnicos: %d series upserted", len(records))
    return len(records)
