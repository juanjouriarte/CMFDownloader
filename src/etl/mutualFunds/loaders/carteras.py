from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Type

import pandas as pd
from sqlalchemy import delete, inspect
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import Base, SessionLocal
from src.db.models.carteras import CarteraExtr, CarteraFutu, CarteraNaci, CarteraOpci, CarteraOpla

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000

NACI_COL_MAP = {
    "ffm_6010100": "nemotecnico",
    "ffm_6010211": "rut_emisor",
    "ffm_6010212": "dv_emisor",
    "ffm_6010300": "codigo_pais_emisor",
    "ffm_6010400": "tipo_instrumento",
    "ffm_6010500": "fecha_vencimiento",
    "ffm_6010600": "situacion_instrumento",
    "ffm_6010700": "clasificacion_riesgo",
    "ffm_6010800": "codigo_grupo_empresarial",
    "ffm_6010900": "cantidad_unidades",
    "ffm_6011000": "tipo_unidades",
    "ffm_tir_6011111": "tir",
    "ffm_par_6011111": "porcentaje_valor_par",
    "ffm_rel_6011111": "valor_relevante",
    "ffm_6011112": "codigo_valorizacion",
    "ffm_6011113": "base_tasa",
    "ffm_6011114": "tipo_interes",
    "ffm_6011200": "valorizacion_cierre",
    "ffm_6011300": "moneda_liquidacion",
    "ffm_6011400": "pais_transaccion",
    "ffm_6011511": "porcentaje_capital_emisor",
    "ffm_6011512": "porcentaje_activos_emisor",
    "ffm_6011513": "porcentaje_activos_fondo",
}

EXTR_COL_MAP = {
    "ffm_6020100": "nemotecnico",
    "ffm_6020200": "nombre_emisor",
    "ffm_6020300": "codigo_pais_emisor",
    "ffm_6020400": "tipo_instrumento",
    "ffm_6020500": "fecha_vencimiento",
    "ffm_6020600": "situacion_instrumento",
    "ffm_6020700": "clasificacion_riesgo",
    "ffm_6020800": "nombre_grupo_empresarial",
    "ffm_6020900": "cantidad_unidades",
    "ffm_6021000": "tipo_unidades",
    "ffm_tir_6021111": "tir",
    "ffm_par_6021111": "porcentaje_valor_par",
    "ffm_rel_6021111": "valor_relevante",
    "ffm_6021112": "codigo_valorizacion",
    "ffm_6021113": "base_tasa",
    "ffm_6021114": "tipo_interes",
    "ffm_6021200": "valorizacion_cierre",
    "ffm_6021300": "moneda_liquidacion",
    "ffm_6021400": "pais_transaccion",
    "ffm_6021511": "porcentaje_capital_emisor",
    "ffm_6021512": "porcentaje_activos_emisor",
    "ffm_6021513": "porcentaje_activos_fondo",
}

OPCI_COL_MAP = {
    "ffm_6030111": "activo_objeto",
    "ffm_6030112": "nemotecnico",
    "ffm_6030113": "forma_ejercicio",
    "ffm_6030114": "fecha_expiracion",
    "ffm_6030115": "moneda_liquidacion",
    "ffm_6030116": "codigo_pais",
    "ffm_6030200": "tipo_opcion",
    "ffm_6030300": "valor_mercado_prima_unitario",
    "ffm_6030400": "numero_contratos",
    "ffm_6030500": "precio_ejercicio",
    "ffm_6030600": "valor_mercado_activo_objeto",
    "ffm_6030700": "unidades_activo_objeto",
    "ffm_6030800": "inversion_primas",
    "ffm_6030900": "valorizacion_precio_ejercicio",
    "ffm_6031000": "valorizacion_mercado",
    "ffm_6031100": "porcentaje_primas_activo_total",
}

FUTU_COL_MAP = {
    "ffm_6040111": "activo_objeto",
    "ffm_6040112": "nemotecnico",
    "ffm_6040113": "unidad_cotizacion",
    "ffm_6040114": "fecha_vencimiento",
    "ffm_6040115": "moneda_liquidacion",
    "ffm_6040116": "codigo_pais",
    "ffm_6040200": "posicion",
    "ffm_6040300": "unidades_nominales_totales",
    "ffm_6040400": "precio_futuro",
    "ffm_6040500": "monto_comprometido",
    "ffm_6040600": "valorizacion_mercado",
}

OPLA_COL_MAP = {
    "ffm_6050111": "activo_objeto",
    "ffm_6050112": "nemotecnico",
    "ffm_6050113": "forma_ejercicio",
    "ffm_6050114": "fecha_expiracion",
    "ffm_6050115": "moneda_liquidacion",
    "ffm_6050116": "codigo_pais",
    "ffm_6050200": "tipo_opcion",
    "ffm_6050300": "valor_mercado_prima_unitario",
    "ffm_6050400": "numero_contratos",
    "ffm_6050500": "precio_ejercicio",
    "ffm_6050600": "valor_mercado_activo_objeto",
    "ffm_6050700": "unidades_activo_objeto",
    "ffm_6050800": "inversion_primas",
    "ffm_6050900": "valorizacion_precio_ejercicio",
    "ffm_6051000": "valorizacion_mercado",
    "ffm_6051100": "porcentaje_primas_activo_total",
}

CARTERA_MODELS: dict[str, Type[Base]] = {
    "NACI": CarteraNaci,
    "EXTR": CarteraExtr,
    "OPCI": CarteraOpci,
    "FUTU": CarteraFutu,
    "OPLA": CarteraOpla,
}

CARTERA_COL_MAPS: dict[str, dict[str, str]] = {
    "NACI": NACI_COL_MAP,
    "EXTR": EXTR_COL_MAP,
    "OPCI": OPCI_COL_MAP,
    "FUTU": FUTU_COL_MAP,
    "OPLA": OPLA_COL_MAP,
}


def _model_columns(model: Type[Base]) -> set[str]:
    return {c.key for c in inspect(model).mapper.column_attrs}


def load_cartera(path: Path, tipo: str, year: int, month: int) -> int:
    model = CARTERA_MODELS[tipo]
    col_map = CARTERA_COL_MAPS[tipo]
    periodo = date(year, month, 1)

    df = pd.read_csv(path, sep=";", dtype=str, encoding="latin-1")
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns=col_map)
    df = df.where(pd.notna(df), None)
    df["periodo"] = periodo
    df = df.dropna(subset=["run_fondo"])

    if df.empty:
        logger.warning("No records in %s", path.name)
        return 0

    known_cols = _model_columns(model)
    records = [{k: v for k, v in r.items() if k in known_cols} for r in df.to_dict(orient="records")]

    with SessionLocal() as session:
        session.execute(delete(model).where(model.periodo == periodo))
        for start in range(0, len(records), BATCH_SIZE):
            session.execute(insert(model).values(records[start : start + BATCH_SIZE]))
        session.commit()

    logger.info("Cartera %s %d-%02d: %d filas cargadas", tipo, year, month, len(records))
    return len(records)
