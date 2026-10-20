"""Reported cash events and FM reparto factors; never infer payment totals."""
from datetime import date
from math import isfinite
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook

router = APIRouter(tags=['fund-distributions'])


class DistributionPoint(BaseModel):
    fecha: date
    serie: str | None
    monto_por_cuota: float | None = None
    monto_total_clp: float | None = None
    tipo: Literal['dividendo', 'distribucion']
    moneda: str | None
    fecha_limite: date | None = None
    fecha_pago: date | None = None
    factor_reparto: float | None = None
    descripcion: str
    nemotecnico: str | None = None
    fuente: Literal['Bolsa de Santiago', 'CMF cartola diaria']


def _day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _number(value):
    return float(value) if value is not None and isfinite(float(value)) else None


def _bounds(start, end):
    if start and end and start > end:
        raise HTTPException(422, 'La fecha inicial debe ser anterior a la final')


@router.get('/investment-funds/{run}/dividends', response_model=list[DistributionPoint])
def fi_distributions(run: str, _: CacheHook, serie: str | None = Query(None),
                     from_date: date | None = Query(None), to_date: date | None = Query(None)):
    _bounds(from_date, to_date)
    # Distinct normalized aliases prevent duplicate CMF ticker mappings from
    # multiplying an event. Preserve different events, currencies and series.
    sql = """WITH tickers AS (
      SELECT DISTINCT serie, REPLACE(UPPER(nemotecnico), '-', '') AS ticker
      FROM nemotecnicos_fi WHERE run_fondo=:run
    ) SELECT DISTINCT n.serie,n.ticker,d.fec_lim,d.fec_pago,d.descrip_vc,d.val_acc,d.moneda
      FROM tickers n JOIN dividendos d ON REPLACE(UPPER(d.nemo), '-', '')=n.ticker
      WHERE (:serie IS NULL OR n.serie=:serie)
        AND (UPPER(d.descrip_vc) LIKE 'DIVIDENDO%' OR UPPER(d.descrip_vc) LIKE 'REPARTO%')
    """
    with SessionLocal() as session:
        records = session.execute(text(sql), dict(run=run, serie=serie)).mappings().all()
    result = []
    for r in records:
        payment, limit = _day(r['fec_pago']), _day(r['fec_lim'])
        event_date = payment or limit
        if not event_date or (from_date and event_date < from_date) or (to_date and event_date > to_date):
            continue
        result.append(DistributionPoint(
            fecha=event_date, serie=r['serie'], monto_por_cuota=_number(r['val_acc']),
            tipo='dividendo' if r['descrip_vc'].upper().startswith('DIVIDENDO') else 'distribucion',
            moneda=r['moneda'], fecha_limite=limit, fecha_pago=payment,
            descripcion=r['descrip_vc'], nemotecnico=r['ticker'], fuente='Bolsa de Santiago'))
    return sorted(result, key=lambda r: (r.fecha, r.serie or '', r.descripcion), reverse=True)


@router.get('/mutual-funds/{run}/dividends', response_model=list[DistributionPoint])
def fm_distributions(run: str, _: CacheHook, serie: str | None = Query(None),
                     from_date: date | None = Query(None), to_date: date | None = Query(None)):
    _bounds(from_date, to_date)
    sql = """SELECT fecha,serie,moneda,factor_reparto FROM cartola_diaria
      WHERE run_fondo=:run AND (:serie IS NULL OR serie=:serie)
        AND (:start IS NULL OR fecha>=:start) AND fecha<=:end
        AND factor_reparto>0 AND factor_reparto<>1
        AND CAST(factor_reparto AS TEXT) NOT IN ('NaN','Infinity','-Infinity')
      ORDER BY fecha DESC,serie"""
    with SessionLocal() as session:
        records = session.execute(text(sql), dict(run=run, serie=serie, start=from_date,
                                                  end=to_date or date.today())).mappings().all()
    return [DistributionPoint(fecha=r['fecha'], serie=r['serie'], moneda=r['moneda'],
                              tipo='distribucion', factor_reparto=_number(r['factor_reparto']),
                              descripcion='Factor de reparto reportado', fuente='CMF cartola diaria')
            for r in records]
