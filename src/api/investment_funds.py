from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/investment-funds", tags=["investment-funds"])


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


class FundFIItem(BaseModel):
    moneda: str | None = None
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    rescatable: bool | None
    vigente: bool | None
    categoria: str | None
    tipo: str | None
    nombre_cat: str | None


class FIPortfolioPosition(BaseModel):
    source: str
    nemotecnico: str | None
    rut_emisor: str | None
    nombre_emisor: str | None
    tipo_instrumento: str | None
    nombre_instrumento: str | None
    pct_activo_fondo: float | None
    valorizacion_cierre: float | None
    clasif_riesgo: str | None
    tir_val_par_precio: float | None
    fecha_vencimiento: str | None
    cant_unidades: float | None
    tipo_unidades: str | None
    cod_moneda_liquidacion: str | None
    nombre_moneda: str | None
    tipo_interes: str | None
    pct_capital_emisor: float | None
    pct_activo_emisor: float | None
    situacion_instrumento: str | None
    clasif_esf: str | None
    cod_pais: str | None
    nombre_pais: str | None
    nombre_fondo_emisor: str | None


class FIPortfolioHistoryPosition(FIPortfolioPosition):
    periodo: date


class EquityActivityPoint(BaseModel):
    periodo: date
    cuotas_emitidas: int
    delta_emitidas: int
    cuotas_pagadas: int
    delta_pagadas: int
    cuotas_suscritas_no_pagadas: int
    num_cuotas_promesa: int
    num_contratos_promesa: int | None
    num_promitentes: int | None
    valor_libro: float | None
    capital_called_clp: float | None
    new_auth_clp: float | None
    pending_formal_clp: float | None
    pending_promise_clp: float | None


class FlowPointFI(BaseModel):
    fecha: date
    aportes: float | None
    rescates: float | None
    nnm: float | None


class NavFI(BaseModel):
    first_date: date | None = None
    serie: str | None
    moneda: str | None
    fecha: date
    valor_cuota: float | None   # valor_libro aliased for consistency with FM
    patrimonio_neto: float | None
    num_aportantes: int | None


class RentFI(BaseModel):
    run_fondo: str
    serie: str | None
    razon_social: str | None
    administrador: str | None
    valor_actual: float | None
    fecha_calculo: date | None
    r_1d: float | None
    r_1w: float | None
    r_1m: float | None
    r_1y: float | None
    r_5y: float | None
    r_ytd: float | None


class FundFIDetail(FundFIItem):
    series: list[NavFI]
    rentability: list[RentFI]
    category: CategoryInfo | None
    geo_breakdown: list[GeoPct]
    series_links: list[dict] = []


class ReturnPointFI(BaseModel):
    fecha: date
    return_pct: float
    source_serie: str | None = None


class NavPointFI(BaseModel):
    fecha: date
    serie: str | None
    valor_cuota: float | None   # valor_libro aliased for consistency with FM
    patrimonio_neto: float | None


@router.get("", response_model=list[FundFIItem])
def list_investment_funds(
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Partial match on administrador name"),
    rescatable: bool | None = Query(None),
    vigente: bool | None = Query(None),
    categoria: str | None = Query(None, description="Category code from categoria_fi_effective"),
    tipo: str | None = Query(None, description="Category type (e.g. 'Accionario', 'Deuda')"),
) -> list[FundFIItem]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("f.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if rescatable is not None:
        conditions.append("f.rescatable = :rescatable")
        params["rescatable"] = rescatable
    if vigente is not None:
        conditions.append("f.vigente = :vigente")
        params["vigente"] = vigente
    if categoria:
        conditions.append("cat.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("cat.tipo = :tipo")
        params["tipo"] = tipo

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT f.run_fondo, f.razon_social, f.administrador, f.rescatable, f.vigente, f.moneda,
               cat.categoria, cat.tipo, cat.nombre_cat
        FROM fondos_inversion f
        LEFT JOIN LATERAL (
            SELECT categoria, tipo, nombre_cat
            FROM categoria_fi_effective
            WHERE run_fondo = f.run_fondo
            ORDER BY periodo DESC
            LIMIT 1
        ) cat ON true
        {where}
        ORDER BY f.razon_social
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundFIItem(**dict(r)) for r in rows]


@router.get("/{run}", response_model=FundFIDetail)
def get_investment_fund(run: str, _: CacheHook) -> FundFIDetail:
    with SessionLocal() as session:
        fund_row = session.execute(
            text("""
                SELECT f.run_fondo, f.razon_social, f.administrador, f.rescatable, f.vigente, f.moneda,
                       cat.categoria, cat.tipo, cat.nombre_cat
                FROM fondos_inversion f
                LEFT JOIN LATERAL (
                    SELECT categoria, tipo, nombre_cat
                    FROM categoria_fi_effective
                    WHERE run_fondo = f.run_fondo
                    ORDER BY periodo DESC
                    LIMIT 1
                ) cat ON true
                WHERE f.run_fondo = :run
            """),
            {"run": run},
        ).mappings().one_or_none()

        if not fund_row:
            raise HTTPException(404, "Investment fund not found")

        from .series_continuity import read_links
        series_links = read_links(session, run)
        series_rows = session.execute(
            text("""
                SELECT DISTINCT ON (serie) serie, moneda, fecha, min(fecha) OVER (PARTITION BY serie) first_date,
                       valor_libro AS valor_cuota, patrimonio_neto, num_aportantes
                FROM valores_cuota_fi v
                WHERE run_fondo = :run AND NOT EXISTS (
                  SELECT 1 FROM fi_series_links l WHERE l.run_fondo=v.run_fondo
                  AND l.source_serie=v.serie AND l.effective_date<=CURRENT_DATE)
                ORDER BY serie, fecha DESC
            """),
            {"run": run},
        ).mappings().all()

        from .fi_detail_returns import latest_series_returns
        rent_rows = latest_series_returns(session, run)

        cat_row = session.execute(
            text("""
                SELECT categoria, tipo, grupo, nombre_cat, confianza, periodo
                FROM categoria_fi_effective
                WHERE run_fondo = :run
                ORDER BY periodo DESC
                LIMIT 1
            """),
            {"run": run},
        ).mappings().one_or_none()

        latest_period = session.execute(
            text("SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run"),
            {"run": run},
        ).scalar_one_or_none()

        geo_rows: list = []
        if latest_period:
            geo_rows = session.execute(
                text("""
                    WITH combined AS (
                        SELECT COALESCE(cod_pais, 'CL') AS pais, pct_activo_fondo AS pct
                        FROM cartera_fi_nac
                        WHERE run_fondo = :run AND periodo = :period AND pct_activo_fondo IS NOT NULL
                        UNION ALL
                        SELECT COALESCE(cod_pais, 'OTHER') AS pais, pct_activo_fondo AS pct
                        FROM cartera_fi_ext
                        WHERE run_fondo = :run AND periodo = :period AND pct_activo_fondo IS NOT NULL
                    )
                    SELECT c.pais, rc.name AS nombre_pais, SUM(c.pct) AS pct_peso
                    FROM combined c
                    LEFT JOIN ref_codes rc ON rc.domain = 'country' AND rc.code = c.pais
                    GROUP BY c.pais, rc.name
                    ORDER BY pct_peso DESC
                """),
                {"run": run, "period": latest_period},
            ).mappings().all()

    return FundFIDetail(
        **dict(fund_row),
        series=[NavFI(**dict(r)) for r in series_rows],
        series_links=series_links,
        rentability=[RentFI(**dict(r)) for r in rent_rows],
        category=CategoryInfo(**dict(cat_row)) if cat_row else None,
        geo_breakdown=[GeoPct(**dict(r)) for r in geo_rows],
    )


@router.get("/{run}/nav", response_model=list[NavPointFI])
def get_investment_fund_nav(
    run: str,
    pagination: Pagination,
    _: CacheHook,
    serie: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
) -> list[NavPointFI]:
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
        SELECT fecha, serie, valor_libro AS valor_cuota, patrimonio_neto
        FROM valores_cuota_fi
        {where}
        ORDER BY fecha DESC, serie
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [NavPointFI(**dict(r)) for r in rows]


@router.get("/{run}/portfolio", response_model=list[FIPortfolioPosition])
def get_investment_fund_portfolio(
    run: str,
    _: CacheHook,
    period: str | None = Query(None, description="Quarter period YYYY-MM-DD, defaults to latest"),
) -> list[FIPortfolioPosition]:
    """Quarter portfolio positions for an investment fund, enriched with SII company names."""
    with SessionLocal() as session:
        if period:
            latest = period
        else:
            latest = session.execute(
                text("SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run"),
                {"run": run},
            ).scalar_one_or_none()

        if not latest:
            return []

        naci = session.execute(
            text("""
                SELECT 'naci' AS source, c.nemotecnico, c.rut_emisor,
                       COALESCE(e.razon_social, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.pct_activo_fondo, c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf,
                       c.cod_pais, rc_pais.name AS nombre_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_nac c
                LEFT JOIN emisores e ON e.rut = c.rut_emisor
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.cod_moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.cod_pais
                WHERE c.run_fondo = :run AND c.periodo = :period
                ORDER BY c.pct_activo_fondo DESC NULLS LAST
            """),
            {"run": run, "period": latest},
        ).mappings().all()

        extr = session.execute(
            text("""
                SELECT 'extr' AS source, c.nemo_isin AS nemotecnico, NULL AS rut_emisor,
                       COALESCE(c.nombre_emisor, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.pct_activo_fondo, c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf,
                       c.cod_pais, rc_pais.name AS nombre_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_ext c
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemo_isin
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemo_isin
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.cod_moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.cod_pais
                WHERE c.run_fondo = :run AND c.periodo = :period
                ORDER BY c.pct_activo_fondo DESC NULLS LAST
            """),
            {"run": run, "period": latest},
        ).mappings().all()

    return [FIPortfolioPosition(**dict(r)) for r in naci] + [FIPortfolioPosition(**dict(r)) for r in extr]


@router.get("/{run}/portfolio/history", response_model=list[FIPortfolioHistoryPosition])
def get_investment_fund_portfolio_history(
    run: str,
    pagination: Pagination,
    _: CacheHook,
) -> list[FIPortfolioHistoryPosition]:
    """All quarterly portfolio positions for a fund across every available period.

    Returns a flat list ordered by periodo DESC. Each item includes the periodo field
    so the frontend can group by quarter. Paginate with ?limit= / ?offset=.
    """
    limit, offset = pagination
    with SessionLocal() as session:
        naci = session.execute(
            text("""
                SELECT c.periodo, 'naci' AS source, c.nemotecnico, c.rut_emisor,
                       COALESCE(e.razon_social, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.pct_activo_fondo, c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf,
                       c.cod_pais, rc_pais.name AS nombre_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_nac c
                LEFT JOIN emisores e ON e.rut = c.rut_emisor
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.cod_moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.cod_pais
                WHERE c.run_fondo = :run
                ORDER BY c.periodo DESC, c.pct_activo_fondo DESC NULLS LAST
                LIMIT :limit OFFSET :offset
            """),
            {"run": run, "limit": limit, "offset": offset},
        ).mappings().all()

        extr = session.execute(
            text("""
                SELECT c.periodo, 'extr' AS source, c.nemo_isin AS nemotecnico, NULL AS rut_emisor,
                       COALESCE(c.nombre_emisor, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, rc_inst.name AS nombre_instrumento,
                       c.pct_activo_fondo, c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, rc_mon.name AS nombre_moneda,
                       c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf,
                       c.cod_pais, rc_pais.name AS nombre_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_ext c
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemo_isin
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemo_isin
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                LEFT JOIN ref_codes rc_inst ON rc_inst.domain = 'instrument' AND rc_inst.code = c.tipo_instrumento
                LEFT JOIN ref_codes rc_mon  ON rc_mon.domain  = 'currency'   AND rc_mon.code  = c.cod_moneda_liquidacion
                LEFT JOIN ref_codes rc_pais ON rc_pais.domain = 'country'    AND rc_pais.code = c.cod_pais
                WHERE c.run_fondo = :run
                ORDER BY c.periodo DESC, c.pct_activo_fondo DESC NULLS LAST
                LIMIT :limit OFFSET :offset
            """),
            {"run": run, "limit": limit, "offset": offset},
        ).mappings().all()

    return [FIPortfolioHistoryPosition(**dict(r)) for r in naci] + [
        FIPortfolioHistoryPosition(**dict(r)) for r in extr
    ]


@router.get("/{run}/equity-activity", response_model=list[EquityActivityPoint])
def get_investment_fund_equity_activity(
    run: str,
    _: CacheHook,
) -> list[EquityActivityPoint]:
    """Quarterly equity activity history for a fund (cuotas_fi).

    Primarily meaningful for non-rescatable (private equity style) FI funds, which call
    capital over time rather than accepting/redeeming daily. cuotas_emitidas and
    cuotas_pagadas are cumulative STOCKS — this computes quarter-over-quarter deltas via
    LAG() to show actual activity per period:

      capital_called_clp   = delta_pagadas × valor_libro (+ called in, - returned)
      new_auth_clp         = delta_emitidas × valor_libro (authorization increase)
      pending_formal_clp   = cuotas_suscritas_no_pagadas × valor_libro (subscribed, uncalled)
      pending_promise_clp  = num_cuotas_promesa × valor_libro (promise-stage commitments)

    Ordered chronologically (oldest first) for charting. First period's deltas are 0
    (no prior quarter to compare against).
    """
    sql = text("""
        WITH history AS (
            SELECT
                cf.periodo,
                COALESCE(cf.cuotas_emitidas, 0)             AS cuotas_emitidas,
                COALESCE(cf.cuotas_pagadas, 0)               AS cuotas_pagadas,
                COALESCE(cf.cuotas_suscritas_no_pagadas, 0)  AS cuotas_suscritas_no_pagadas,
                COALESCE(cf.num_cuotas_promesa, 0)           AS num_cuotas_promesa,
                cf.num_contratos_promesa,
                cf.num_promitentes,
                cf.valor_libro,
                LAG(COALESCE(cf.cuotas_emitidas, 0)) OVER (ORDER BY cf.periodo) AS prev_emitidas,
                LAG(COALESCE(cf.cuotas_pagadas, 0))  OVER (ORDER BY cf.periodo) AS prev_pagadas
            FROM cuotas_fi cf
            WHERE cf.run_fondo = :run
              AND cf.valor_libro IS NOT NULL AND cf.valor_libro > 0
        )
        SELECT
            periodo,
            cuotas_emitidas,
            cuotas_emitidas - COALESCE(prev_emitidas, cuotas_emitidas) AS delta_emitidas,
            cuotas_pagadas,
            cuotas_pagadas - COALESCE(prev_pagadas, cuotas_pagadas)    AS delta_pagadas,
            cuotas_suscritas_no_pagadas,
            num_cuotas_promesa, num_contratos_promesa, num_promitentes,
            valor_libro,
            (cuotas_pagadas - COALESCE(prev_pagadas, cuotas_pagadas)) * valor_libro  AS capital_called_clp,
            (cuotas_emitidas - COALESCE(prev_emitidas, cuotas_emitidas)) * valor_libro AS new_auth_clp,
            cuotas_suscritas_no_pagadas * valor_libro AS pending_formal_clp,
            num_cuotas_promesa * valor_libro          AS pending_promise_clp
        FROM history
        ORDER BY periodo
    """)
    with SessionLocal() as session:
        rows = session.execute(sql, {"run": run}).mappings().all()
    return [EquityActivityPoint(**dict(r)) for r in rows]


@router.get("/{run}/flows", response_model=list[FlowPointFI])
def get_investment_fund_flows(
    run: str,
    _: CacheHook,
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
) -> list[FlowPointFI]:
    """Net new money history for an investment fund.

    Rescatable funds: daily implied net flow (valores_cuota_fi.flujo_neto), aggregated
    monthly. Non-rescatable funds: quarterly net capital called — quarter-over-quarter delta
    of cuotas_pagadas (capital actually paid in, not just authorized via cuotas_emitidas) ×
    valor_libro, same methodology as capital_called_clp in /equity-activity. Gross
    aportes/rescates aren't separable from this data for FI, so only nnm is populated.
    """
    with SessionLocal() as session:
        rescatable = session.execute(
            text("SELECT rescatable FROM fondos_inversion WHERE run_fondo = :run"),
            {"run": run},
        ).scalar_one_or_none()

        if rescatable is None:
            raise HTTPException(404, "Investment fund not found")

        if rescatable:
            conditions = ["run_fondo = :run", "flujo_neto IS NOT NULL"]
            params: dict = {"run": run}
            if from_date:
                conditions.append("fecha >= :from_date")
                params["from_date"] = from_date
            if to_date:
                conditions.append("fecha <= :to_date")
                params["to_date"] = to_date
            where = "WHERE " + " AND ".join(conditions)
            rows = session.execute(
                text(f"""
                    SELECT DATE_TRUNC('month', fecha)::date AS fecha,
                           NULL::numeric AS aportes,
                           NULL::numeric AS rescates,
                           SUM(flujo_neto) AS nnm
                    FROM valores_cuota_fi
                    {where}
                    GROUP BY DATE_TRUNC('month', fecha)
                    ORDER BY fecha DESC
                """),
                params,
            ).mappings().all()
        else:
            extra = []
            params = {"run": run}
            if from_date:
                extra.append("AND periodo >= :from_date")
                params["from_date"] = from_date
            if to_date:
                extra.append("AND periodo <= :to_date")
                params["to_date"] = to_date
            rows = session.execute(
                text(f"""
                    WITH history AS (
                        SELECT cf.periodo, cf.valor_libro,
                               COALESCE(cf.cuotas_pagadas, 0) AS cuotas_pagadas,
                               LAG(COALESCE(cf.cuotas_pagadas, 0)) OVER (ORDER BY cf.periodo) AS prev_pagadas
                        FROM cuotas_fi cf
                        WHERE cf.run_fondo = :run
                          AND cf.valor_libro IS NOT NULL AND cf.valor_libro > 0
                    )
                    SELECT periodo AS fecha,
                           NULL::numeric AS aportes,
                           NULL::numeric AS rescates,
                           (cuotas_pagadas - COALESCE(prev_pagadas, cuotas_pagadas)) * valor_libro AS nnm
                    FROM history
                    WHERE 1=1 {" ".join(extra)}
                    ORDER BY fecha DESC
                """),
                params,
            ).mappings().all()

    return [FlowPointFI(**dict(r)) for r in rows]


@router.get("/{run}/return-series", response_model=list[ReturnPointFI])
def get_investment_fund_return_series(
    run: str,
    _: CacheHook,
    serie: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    linked: bool = Query(True, description="Include confirmed predecessor series; false returns original series only."),
) -> list[ReturnPointFI]:
    """Additive NAV + distributions, in the selected series' unit basis.

    Confirmed chains adjust NAV and distributions by documented conversion ratios.
    Raw observations and patrimonio remain unchanged. Baseline is the latest valid
    NAV on/before from_date; missing baselines never become synthetic zero returns.
    """
    effective_from = from_date or (date.today() - timedelta(days=365))
    end = min(to_date or date.today(), date.today())
    if effective_from > end:
        raise HTTPException(422, "La fecha inicial debe ser anterior a la fecha final.")
    with SessionLocal() as session:
        if not serie:
            serie = session.execute(text("""SELECT serie FROM valores_cuota_fi
                WHERE run_fondo=:run AND fecha<=:end
                ORDER BY fecha DESC,patrimonio_neto DESC NULLS LAST LIMIT 1"""),
                dict(run=run,end=end)).scalar_one_or_none()
            if serie is None:
                return []
        rows = session.execute(text("""
          WITH nav AS MATERIALIZED (SELECT * FROM fi_nav_history(:run,:serie,:end,:linked)),
          base AS (
            SELECT * FROM nav WHERE fecha<=:start AND fecha>=CAST(:start AS date)-7 AND valor_libro>0
            AND valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
            AND currency ~ '^(?:[A-Z]{3}|UF)$' ORDER BY fecha DESC LIMIT 1
          ), divs AS MATERIALIZED (SELECT * FROM fi_series_distributions(:run,:serie,:end,:linked))
          SELECT n.fecha,n.source_serie,
            round((n.valor_libro-b.valor_libro+COALESCE((SELECT sum(d.amount) FROM divs d
              WHERE d.fecha>b.fecha AND d.fecha<=n.fecha AND d.currency=b.currency),0))
              /b.valor_libro*100,4) return_pct
          FROM nav n CROSS JOIN base b WHERE n.fecha>=b.fecha AND n.valor_libro>0
            AND n.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND n.currency=b.currency
            AND NOT EXISTS(SELECT 1 FROM nav other WHERE other.fecha BETWEEN b.fecha AND n.fecha
                AND other.currency IS DISTINCT FROM b.currency)
          ORDER BY n.fecha
        """),dict(run=run,serie=serie,start=effective_from,end=end,linked=linked)).mappings().all()
    return [ReturnPointFI(**dict(row)) for row in rows]
