from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/mutual-funds", tags=["mutual-funds"])

_VALID_SORT = {"r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"}


class CategoryInfo(BaseModel):
    categoria: str
    tipo: str
    grupo: str
    nombre_cat: str
    confianza: str
    periodo: date


class GeoPct(BaseModel):
    pais: str
    nombre_pais: str | None
    pct_peso: float


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
    categoria: str | None
    tipo: str | None
    nombre_cat: str | None


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
    is_data_suspicious: bool
    suspicious_periods: list[str]


class TACInfo(BaseModel):
    periodo: date
    tac_total: float | None
    tac_rem_fija: float | None
    tac_rem_var: float | None
    tac_gastos_op: float | None


class FlowPoint(BaseModel):
    fecha: date
    aportes: float | None
    rescates: float | None
    nnm: float | None


class FundFMDetail(FundFMItem):
    fecha_termino_operaciones: date | None
    series: list[NavSerie]
    rentability: list[RentFM]
    category: CategoryInfo | None
    geo_breakdown: list[GeoPct]
    latest_tac: TACInfo | None


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
    nombre_instrumento: str | None
    porcentaje_activos_fondo: str | None
    valorizacion_cierre: str | None
    clasificacion_riesgo: str | None
    tir: str | None
    fecha_vencimiento: str | None
    cantidad_unidades: str | None
    tipo_unidades: str | None
    moneda_liquidacion: str | None
    nombre_moneda: str | None
    porcentaje_valor_par: str | None
    tipo_interes: str | None
    codigo_pais_emisor: str | None
    nombre_pais: str | None
    situacion_instrumento: str | None
    porcentaje_capital_emisor: str | None
    porcentaje_activos_emisor: str | None
    codigo_grupo_empresarial: str | None
    nombre_fondo_emisor: str | None


@router.get("", response_model=list[FundFMItem])
def list_funds(
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Partial match on administrador name"),
    tipo_fondo: str | None = Query(None),
    vigente: bool | None = Query(None),
    categoria: str | None = Query(None, description="Category code from categoria_fm"),
    tipo: str | None = Query(None, description="Category type (e.g. 'Accionario', 'Deuda')"),
) -> list[FundFMItem]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("f.razon_social_administradora ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if tipo_fondo:
        conditions.append("f.tipo_fondo = :tipo_fondo")
        params["tipo_fondo"] = tipo_fondo
    if vigente is not None:
        conditions.append(
            "f.fecha_termino_operaciones IS NULL"
            if vigente
            else "f.fecha_termino_operaciones IS NOT NULL"
        )
    if categoria:
        conditions.append("cat.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("cat.tipo = :tipo")
        params["tipo"] = tipo

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT f.run_fondo, f.nombre_fondo, f.nombre_corto, f.rut_administradora,
               f.razon_social_administradora AS administrador, f.tipo_fondo, f.moneda,
               f.fecha_inicio_operaciones,
               f.fecha_termino_operaciones IS NULL AS vigente,
               cat.categoria, cat.tipo, cat.nombre_cat
        FROM fondo_mutuo f
        LEFT JOIN LATERAL (
            SELECT categoria, tipo, nombre_cat
            FROM categoria_fm
            WHERE run_fondo = f.run_fondo
            ORDER BY periodo DESC
            LIMIT 1
        ) cat ON true
        {where}
        ORDER BY f.nombre_fondo
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
                SELECT f.run_fondo, f.nombre_fondo, f.nombre_corto, f.rut_administradora,
                       f.razon_social_administradora AS administrador, f.tipo_fondo, f.moneda,
                       f.fecha_inicio_operaciones, f.fecha_termino_operaciones,
                       f.fecha_termino_operaciones IS NULL AS vigente,
                       cat.categoria, cat.tipo, cat.nombre_cat
                FROM fondo_mutuo f
                LEFT JOIN LATERAL (
                    SELECT categoria, tipo, nombre_cat
                    FROM categoria_fm
                    WHERE run_fondo = f.run_fondo
                    ORDER BY periodo DESC
                    LIMIT 1
                ) cat ON true
                WHERE f.run_fondo = :run
            """),
            {"run": run},
        ).mappings().one_or_none()

        if not fund_row:
            raise HTTPException(404, "Fund not found")

        series_rows = session.execute(
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
                       fecha_calculo, r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd,
                       is_data_suspicious, suspicious_periods
                FROM v_rentabilidad_fm_quality WHERE run_fondo = :run
            """),
            {"run": run},
        ).mappings().all()

        cat_row = session.execute(
            text("""
                SELECT categoria, tipo, grupo, nombre_cat, confianza, periodo
                FROM categoria_fm
                WHERE run_fondo = :run
                ORDER BY periodo DESC
                LIMIT 1
            """),
            {"run": run},
        ).mappings().one_or_none()

        latest_period = session.execute(
            text("SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run"),
            {"run": run},
        ).scalar_one_or_none()

        geo_rows: list = []
        if latest_period:
            geo_rows = session.execute(
                text("""
                    WITH combined AS (
                        SELECT COALESCE(codigo_pais_emisor, 'CL') AS pais,
                               CAST(porcentaje_activos_fondo AS numeric) AS pct
                        FROM cartera_naci
                        WHERE run_fondo = :run AND periodo = :period
                          AND porcentaje_activos_fondo IS NOT NULL
                          AND porcentaje_activos_fondo ~ '^-?[0-9]+(\.[0-9]+)?$'
                        UNION ALL
                        SELECT COALESCE(codigo_pais_emisor, 'OTHER') AS pais,
                               CAST(porcentaje_activos_fondo AS numeric) AS pct
                        FROM cartera_extr
                        WHERE run_fondo = :run AND periodo = :period
                          AND porcentaje_activos_fondo IS NOT NULL
                          AND porcentaje_activos_fondo ~ '^-?[0-9]+(\.[0-9]+)?$'
                    )
                    SELECT c.pais, rc.name AS nombre_pais, SUM(c.pct) AS pct_peso
                    FROM combined c
                    LEFT JOIN ref_codes rc ON rc.domain = 'country' AND rc.code = c.pais
                    GROUP BY c.pais, rc.name
                    ORDER BY pct_peso DESC
                """),
                {"run": run, "period": latest_period},
            ).mappings().all()

        tac_row = session.execute(
            text("""
                SELECT periodo, tac_total, tac_rem_fija, tac_rem_var, tac_gastos_op
                FROM tac
                WHERE run_fondo = :run
                ORDER BY periodo DESC
                LIMIT 1
            """),
            {"run": run},
        ).mappings().one_or_none()

    return FundFMDetail(
        **dict(fund_row),
        series=[NavSerie(**dict(r)) for r in series_rows],
        rentability=[RentFM(**dict(r)) for r in rent_rows],
        category=CategoryInfo(**dict(cat_row)) if cat_row else None,
        geo_breakdown=[GeoPct(**dict(r)) for r in geo_rows],
        latest_tac=TACInfo(**dict(tac_row)) if tac_row else None,
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


@router.get("/{run}/tac", response_model=list[TACInfo])
def get_fund_tac(run: str, _: CacheHook) -> list[TACInfo]:
    """TAC history for a mutual fund — last 24 months, descending."""
    sql = text("""
        SELECT periodo, tac_total, tac_rem_fija, tac_rem_var, tac_gastos_op
        FROM tac
        WHERE run_fondo = :run
        ORDER BY periodo DESC
        LIMIT 24
    """)
    with SessionLocal() as session:
        rows = session.execute(sql, {"run": run}).mappings().all()
    return [TACInfo(**dict(r)) for r in rows]


@router.get("/{run}/flows", response_model=list[FlowPoint])
def get_fund_flows(
    run: str,
    _: CacheHook,
    serie: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
) -> list[FlowPoint]:
    """Monthly aportes, rescates, and net new money — aggregated across all series unless ?serie= specified."""
    conditions = ["run_fondo = :run"]
    params: dict = {"run": run}

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
        SELECT DATE_TRUNC('month', fecha)::date AS fecha,
               SUM(monto_aportado) AS aportes,
               SUM(monto_rescatado) AS rescates,
               SUM(monto_aportado - monto_rescatado) AS nnm
        FROM cartola_diaria
        {where}
        GROUP BY DATE_TRUNC('month', fecha)
        ORDER BY fecha DESC
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FlowPoint(**dict(r)) for r in rows]


@router.get("/{run}/portfolio", response_model=list[PortfolioPosition])
def get_fund_portfolio(
    run: str,
    _: CacheHook,
    period: str | None = Query(None, description="Month period YYYY-MM-DD, defaults to latest"),
) -> list[PortfolioPosition]:
    with SessionLocal() as session:
        if period:
            latest = period
        else:
            latest = session.execute(
                text("SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run"),
                {"run": run},
            ).scalar_one_or_none()

        if not latest:
            return []

        naci = session.execute(
            text("""
                SELECT 'naci' AS source, c.nemotecnico, c.rut_emisor,
                       COALESCE(e.razon_social, fm.nombre_fondo, fi.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.porcentaje_activos_fondo, c.valorizacion_cierre, c.clasificacion_riesgo,
                       c.tir, c.fecha_vencimiento, c.cantidad_unidades, c.tipo_unidades,
                       c.moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.porcentaje_valor_par, c.tipo_interes,
                       c.codigo_pais_emisor, rc_pais.name AS nombre_pais,
                       c.situacion_instrumento, c.porcentaje_capital_emisor,
                       c.porcentaje_activos_emisor, c.codigo_grupo_empresarial,
                       COALESCE(fm.nombre_fondo, fi.razon_social) AS nombre_fondo_emisor
                FROM cartera_naci c
                LEFT JOIN emisores e ON e.rut = c.rut_emisor
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
                LEFT JOIN fondos_inversion fi ON fi.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.codigo_pais_emisor
                WHERE c.run_fondo = :run AND c.periodo = :period
            """),
            {"run": run, "period": latest},
        ).mappings().all()

        extr = session.execute(
            text("""
                SELECT 'extr' AS source, c.nemotecnico, NULL AS rut_emisor,
                       COALESCE(c.nombre_emisor, fm.nombre_fondo, fi.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.porcentaje_activos_fondo, c.valorizacion_cierre, c.clasificacion_riesgo,
                       c.tir, c.fecha_vencimiento, c.cantidad_unidades, c.tipo_unidades,
                       c.moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.porcentaje_valor_par, c.tipo_interes,
                       c.codigo_pais_emisor, rc_pais.name AS nombre_pais,
                       c.situacion_instrumento, c.porcentaje_capital_emisor,
                       c.porcentaje_activos_emisor,
                       c.nombre_grupo_empresarial AS codigo_grupo_empresarial,
                       COALESCE(fm.nombre_fondo, fi.razon_social) AS nombre_fondo_emisor
                FROM cartera_extr c
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
                LEFT JOIN fondos_inversion fi ON fi.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.codigo_pais_emisor
                WHERE c.run_fondo = :run AND c.periodo = :period
            """),
            {"run": run, "period": latest},
        ).mappings().all()

    return [PortfolioPosition(**dict(r)) for r in naci] + [PortfolioPosition(**dict(r)) for r in extr]
