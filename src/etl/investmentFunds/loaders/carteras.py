from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.carteras_fi import (
    CarteraFINac, CarteraFIExt, CarteraFIMetPart, CarteraFIFutFw,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 2_000


def _dec(s: str) -> Decimal | None:
    if not s or s.strip() in ("-", "", "N/A"):
        return None
    s = s.strip()
    try:
        if "," in s:
            return Decimal(s.replace(".", "").replace(",", "."))
        elif s.count(".") > 1:
            return Decimal(s.replace(".", ""))
        else:
            return Decimal(s)
    except InvalidOperation:
        return None


def _rows(table) -> list[list[str]]:
    if table is None:
        return []
    return [
        [c.get_text(strip=True) for c in row.find_all("td")]
        for row in table.find_all("tr")[1:]
        if row.find_all("td") and row.find_all("td")[0].get_text(strip=True).isdigit()
    ]


def parse_nac(html: str, run_fondo: str, periodo: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for c in _rows(soup.find("table")):
        if len(c) < 21: continue
        records.append({
            "run_fondo": run_fondo, "periodo": periodo,
            "clasif_esf": c[0] or None,
            "nemotecnico": c[1] or None,
            "rut_emisor": c[2] or None,
            "cod_pais": c[3] or None,
            "tipo_instrumento": c[4] or None,
            "fecha_vencimiento": c[5] or None,
            "situacion_instrumento": c[6] or None,
            "clasif_riesgo": c[7] or None,
            "grupo_empresarial": c[8] or None,
            "cant_unidades": _dec(c[9]),
            "tipo_unidades": c[10] or None,
            "tir_val_par_precio": _dec(c[11]),
            "cod_valorizacion": c[12] or None,
            "base_tasa": c[13] or None,
            "tipo_interes": c[14] or None,
            "valorizacion_cierre": _dec(c[15]),
            "cod_moneda_liquidacion": c[16] or None,
            "cod_pais_transaccion": c[17] or None,
            "pct_capital_emisor": _dec(c[18]),
            "pct_activo_emisor": _dec(c[19]),
            "pct_activo_fondo": _dec(c[20]),
        })
    return records


def parse_ext(html: str, run_fondo: str, periodo: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for c in _rows(soup.find("table")):
        if len(c) < 22: continue
        records.append({
            "run_fondo": run_fondo, "periodo": periodo,
            "clasif_esf": c[0] or None,
            "nemo_isin": c[1] or None,
            "nombre_emisor_corto": c[2] or None,
            "nombre_emisor": c[3] or None,
            "cod_pais": c[4] or None,
            "tipo_instrumento": c[5] or None,
            "fecha_vencimiento": c[6] or None,
            "situacion_instrumento": c[7] or None,
            "clasif_riesgo": c[8] or None,
            # c[9] = grupo_empresarial (often empty for foreign issuers)
            "cant_unidades": _dec(c[10]),
            "tipo_unidades": c[11] or None,
            "tir_val_par_precio": _dec(c[12]),
            "cod_valorizacion": c[13] or None,
            "base_tasa": c[14] or None,
            "tipo_interes": c[15] or None,
            "valorizacion_cierre": _dec(c[16]),
            "cod_moneda_liquidacion": c[17] or None,
            "cod_pais_transaccion": c[18] or None,
            "pct_capital_emisor": _dec(c[19]),
            "pct_activo_emisor": _dec(c[20]),
            "pct_activo_fondo": _dec(c[21]),
        })
    return records


def parse_met_part(html: str, run_fondo: str, periodo: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for c in _rows(soup.find("table")):
        if len(c) < 15: continue
        records.append({
            "run_fondo": run_fondo, "periodo": periodo,
            "clasif_esf": c[0] or None,
            "nemotecnico": c[1] or None,
            "rut_emisor": c[2] or None,
            "cod_pais": c[3] or None,
            "tipo_instrumento": c[4] or None,
            "situacion_instrumento": c[5] or None,
            "acciones_suscritas_pag": _dec(c[6]),
            "pct_participacion": _dec(c[7]),
            "patrimonio_contable": _dec(c[8]),
            "valor_contable": _dec(c[9]),
            "ajuste_valor_pat": _dec(c[10]),
            "correc_monetaria": _dec(c[11]),
            "cod_moneda": c[12] or None,
            "cod_pais_transaccion": c[13] or None,
            "pct_activo_fondo": _dec(c[14]),
        })
    return records


def parse_fut_fw(html: str, run_fondo: str, periodo: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for c in _rows(soup.find("table")):
        if len(c) < 14: continue
        records.append({
            "run_fondo": run_fondo, "periodo": periodo,
            "activo_subyacente": c[1] or None,
            "tipo_contrato": c[2] or None,
            "cod_moneda_precio": c[3] or None,
            "fecha_inicio": c[4] or None,
            "fecha_vencimiento": c[5] or None,
            "contraparte": c[6] or None,
            "cod_moneda_subyacente": c[7] or None,
            "cod_pais": c[8] or None,
            "posicion": c[9] or None,
            "monto_nominal": _dec(c[10]),
            "precio_pactado": _dec(c[11]),
            "valor_razonable_activo": _dec(c[12]),
            "valor_razonable_pasivo": _dec(c[13]),
        })
    return records


def load_carteras(nac: list[dict], ext: list[dict],
                  met_part: list[dict], fut_fw: list[dict],
                  run_fondo: str, periodo: date) -> int:
    total = 0
    with SessionLocal() as session:
        for model, records in [
            (CarteraFINac,     nac),
            (CarteraFIExt,     ext),
            (CarteraFIMetPart, met_part),
        ]:
            if not records:
                continue
            # Delete + reinsert to handle position changes cleanly
            from sqlalchemy import delete
            session.execute(
                delete(model).where(
                    model.run_fondo == run_fondo,
                    model.periodo == periodo,
                )
            )
            for start in range(0, len(records), BATCH_SIZE):
                session.execute(insert(model).values(records[start:start + BATCH_SIZE]))
            total += len(records)

        if fut_fw:
            from sqlalchemy import delete
            session.execute(
                delete(CarteraFIFutFw).where(
                    CarteraFIFutFw.run_fondo == run_fondo,
                    CarteraFIFutFw.periodo == periodo,
                )
            )
            for start in range(0, len(fut_fw), BATCH_SIZE):
                session.execute(insert(CarteraFIFutFw).values(fut_fw[start:start + BATCH_SIZE]))
            total += len(fut_fw)

        session.commit()

    return total
