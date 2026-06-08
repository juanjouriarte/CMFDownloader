from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/funds", tags=["mutual-funds"])

_VALID_SORT = {"r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"}


class FundFMItem(BaseModel):
    run_fondo: str
    nombre_fondo: str
    nombre_corto: str | None
    rut_administradora: str
    administrador: str
    tipo_fondo: str | None
    moneda: str | None
    vigente: bool
    fecha_inicio_operaciones: date | None


class NavSerie(BaseModel):
    serie: str | None
    moneda: str | None
    fecha: date
    valor_cuota: float | None
    patrimonio_neto: float | None
    num_participes: int | None


class RentFM(BaseModel):
    run_fondo: str
    serie: str | None
    nombre_fondo: str | None
    administrador: str | None
    valor_actual: float | None
    fecha_calculo: date | None
    r_1d: float | None
    r_1w: float | None
    r_1m: float | None
    r_1y: float | None
    r_5y: float | None
    r_ytd: float | None


class FundFMDetail(FundFMItem):
    fecha_termino_operaciones: date | None
    nav: list[NavSerie]
    rentability: list[RentFM]


class NavPoint(BaseModel):
    fecha: date
    serie: str | None
    valor_cuota: float | None


class PortfolioPosition(BaseModel):
    source: str
    nemotecnico: str | None
    rut_emisor: str | None
    nombre_emisor: str | None
    tipo_instrumento: str | None
    porcentaje_activos_fondo: str | None
    valorizacion_cierre: str | None
    clasificacion_riesgo: str | None
    tir: str | None
    fecha_vencimiento: str | None
    cantidad_unidades: str | None
    tipo_unidades: str | None
    moneda_liquidacion: str | None
    porcentaje_valor_par: str | None
    tipo_interes: str | None
    codigo_pais_emisor: str | None
    situacion_instrumento: str | None
    porcentaje_capital_emisor: str | None
    porcentaje_activos_emisor: str | None
    codigo_grupo_empresarial: str | None


@router.get("", response_model=list[FundFMItem])
def list_funds(
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Partial match on administrador name"),
    tipo_fondo: str | None = Query(None),
    vigente: bool | None = Query(None),
) -> list[FundFMItem]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("razon_social_administradora ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if tipo_fondo:
        conditions.append("tipo_fondo = :tipo_fondo")
        params["tipo_fondo"] = tipo_fondo
    if vigente is not None:
        conditions.append(
            "fecha_termino_operaciones IS NULL"
            if vigente
            else "fecha_termino_operaciones IS NOT NULL"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT run_fondo, nombre_fondo, nombre_corto, rut_administradora,
               razon_social_administradora AS administrador, tipo_fondo, moneda,
               fecha_inicio_operaciones,
               fecha_termino_operaciones IS NULL AS vigente
        FROM fondo_mutuo
        {where}
        ORDER BY nombre_fondo
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundFMItem(**dict(r)) for r in rows]


@router.get("/{run}", response_model=FundFMDetail)
def get_fund(run: str, _: CacheHook) -> FundFMDetail:
    with SessionLocal() as session:
        fund_row = session.execute(
            text("""
                SELECT run_fondo, nombre_fondo, nombre_corto, rut_administradora,
                       razon_social_administradora AS administrador, tipo_fondo, moneda,
                       fecha_inicio_operaciones, fecha_termino_operaciones,
                       fecha_termino_operaciones IS NULL AS vigente
                FROM fondo_mutuo WHERE run_fondo = :run
            """),
            {"run": run},
        ).mappings().one_or_none()

        if not fund_row:
            raise HTTPException(404, "Fund not found")

        nav_rows = session.execute(
            text("""
                SELECT DISTINCT ON (serie) serie, moneda, fecha, valor_cuota,
                       patrimonio_neto, num_participes
                FROM cartola_diaria
                WHERE run_fondo = :run
                ORDER BY serie, fecha DESC
            """),
            {"run": run},
        ).mappings().all()

        rent_rows = session.execute(
            text("""
                SELECT run_fondo, serie, nombre_fondo, administrador, valor_actual,
                       fecha_calculo, r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd
                FROM mv_rentabilidad_fm WHERE run_fondo = :run
            """),
            {"run": run},
        ).mappings().all()

    return FundFMDetail(
        **dict(fund_row),
        nav=[NavSerie(**dict(r)) for r in nav_rows],
        rentability=[RentFM(**dict(r)) for r in rent_rows],
    )


@router.get("/{run}/nav", response_model=list[NavPoint])
def get_fund_nav(
    run: str,
    pagination: Pagination,
    _: CacheHook,
    serie: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
) -> list[NavPoint]:
    limit, offset = pagination
    conditions = ["run_fondo = :run"]
    params: dict = {"run": run, "limit": limit, "offset": offset}

    if serie:
        conditions.append("serie = :serie")
        params["serie"] = serie
    if from_date:
        conditions.append("fecha >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("fecha <= :to_date")
        params["to_date"] = to_date

    where = "WHERE " + " AND ".join(conditions)
    sql = text(f"""
        SELECT fecha, serie, valor_cuota
        FROM cartola_diaria
        {where}
        ORDER BY fecha DESC, serie
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [NavPoint(**dict(r)) for r in rows]


@router.get("/{run}/portfolio", response_model=list[PortfolioPosition])
def get_fund_portfolio(run: str, _: CacheHook) -> list[PortfolioPosition]:
    with SessionLocal() as session:
        latest = session.execute(
            text("SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run"),
            {"run": run},
        ).scalar_one_or_none()

        if not latest:
            return []

        naci = session.execute(
            text("""
                SELECT 'naci' AS source, c.nemotecnico, c.rut_emisor,
                       e.razon_social AS nombre_emisor,
                       c.tipo_instrumento, c.porcentaje_activos_fondo,
                       c.valorizacion_cierre, c.clasificacion_riesgo,
                       c.tir, c.fecha_vencimiento, c.cantidad_unidades, c.tipo_unidades,
                       c.moneda_liquidacion, c.porcentaje_valor_par, c.tipo_interes,
                       c.codigo_pais_emisor, c.situacion_instrumento,
                       c.porcentaje_capital_emisor, c.porcentaje_activos_emisor,
                       c.codigo_grupo_empresarial
                FROM cartera_naci c
                LEFT JOIN emisores e ON e.rut = c.rut_emisor
                WHERE c.run_fondo = :run AND c.periodo = :period
            """),
            {"run": run, "period": latest},
        ).mappings().all()

        extr = session.execute(
            text("""
                SELECT 'extr' AS source, c.nemotecnico, NULL AS rut_emisor,
                       c.nombre_emisor,
                       c.tipo_instrumento, c.porcentaje_activos_fondo,
                       c.valorizacion_cierre, c.clasificacion_riesgo,
                       c.tir, c.fecha_vencimiento, c.cantidad_unidades, c.tipo_unidades,
                       c.moneda_liquidacion, c.porcentaje_valor_par, c.tipo_interes,
                       c.codigo_pais_emisor, c.situacion_instrumento,
                       c.porcentaje_capital_emisor, c.porcentaje_activos_emisor,
                       c.nombre_grupo_empresarial AS codigo_grupo_empresarial
                FROM cartera_extr c
                WHERE c.run_fondo = :run AND c.periodo = :period
            """),
            {"run": run, "period": latest},
        ).mappings().all()

    return [PortfolioPosition(**dict(r)) for r in naci] + [PortfolioPosition(**dict(r)) for r in extr]
