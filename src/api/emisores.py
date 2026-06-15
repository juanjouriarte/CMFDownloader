from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/emisores", tags=["emisores"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EmisorItem(BaseModel):
    rut: str
    dv: str | None
    razon_social: str | None
    fm_exposure_clp: float | None
    fi_exposure_clp: float | None
    total_exposure_clp: float | None
    fm_funds_count: int
    fi_funds_count: int
    total_funds_count: int
    fm_agfs_count: int
    fi_agfs_count: int
    total_agfs_count: int
    latest_fm_period: date | None
    latest_fi_period: date | None


class InstrumentBreakdown(BaseModel):
    tipo_instrumento: str | None
    nombre_instrumento: str | None
    exposure_clp: float
    funds_count: int
    pct_of_total: float | None


class TopFundHolding(BaseModel):
    fund_type: str
    run_fondo: str
    nombre_fondo: str | None
    administrador: str | None
    exposure_clp: float | None
    pct_activo_fondo: float | None
    tipo_instrumento: str | None
    nombre_instrumento: str | None
    periodo: date


class EmisorDetail(BaseModel):
    rut: str
    dv: str | None
    razon_social: str | None
    fm_exposure_clp: float | None
    fi_exposure_clp: float | None
    total_exposure_clp: float | None
    fm_funds_count: int
    fi_funds_count: int
    total_funds_count: int
    fm_agfs_count: int
    fi_agfs_count: int
    total_agfs_count: int
    latest_fm_period: date | None
    latest_fi_period: date | None
    instrument_breakdown: list[InstrumentBreakdown]
    top_funds: list[TopFundHolding]


class FundHolding(BaseModel):
    fund_type: str
    run_fondo: str
    nombre_fondo: str | None
    administrador: str | None
    exposure_clp: float | None
    pct_activo_fondo: float | None
    tipo_instrumento: str | None
    nombre_instrumento: str | None
    periodo: date


class HistoryPoint(BaseModel):
    periodo: date
    fund_type: str
    exposure_clp: float
    funds_count: int
    agfs_count: int


class FundEmisorHistoryPoint(BaseModel):
    periodo: date
    fund_type: str
    run_fondo: str
    nombre_fondo: str | None
    administrador: str | None
    exposure_clp: float | None
    pct_activo_fondo: float | None
    tipo_instrumento: str | None
    nombre_instrumento: str | None


class EmisorPosition(BaseModel):
    # identity
    fund_type: str
    run_fondo: str
    nombre_fondo: str | None
    administrador: str | None
    periodo: date
    # instrument classification
    tipo_instrumento: str | None
    nombre_instrumento: str | None
    nemotecnico: str | None
    situacion_instrumento: str | None
    clasificacion_riesgo: str | None
    # valuation
    valorizacion_cierre: float | None
    moneda_liquidacion: str | None
    pct_activo_fondo: float | None
    # debt-specific
    tir: float | None
    fecha_vencimiento: str | None
    tipo_interes: str | None
    base_tasa: str | None
    porcentaje_valor_par: float | None
    # equity-specific
    cantidad_unidades: float | None
    tipo_unidades: str | None
    porcentaje_capital_emisor: float | None
    porcentaje_activos_emisor: float | None


class ConcentrationAGF(BaseModel):
    administrador: str | None
    rut_administradora: str | None
    fund_type: str
    exposure_clp: float
    pct_of_total: float
    funds_count: int


class ConcentrationResult(BaseModel):
    rut: str
    razon_social: str | None
    total_exposure_clp: float
    herfindahl_index: float | None
    concentration_label: str
    agfs: list[ConcentrationAGF]


# ---------------------------------------------------------------------------
# Shared SQL fragments
# ---------------------------------------------------------------------------

_FM_EXPOSURE_CTE = """
latest_fm AS (
    SELECT MAX(periodo) AS p FROM cartera_naci
),
fm_exp AS (
    SELECT
        cn.rut_emisor,
        SUM(CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN cn.valorizacion_cierre::numeric ELSE 0 END)   AS clp,
        COUNT(DISTINCT cn.run_fondo)                                AS funds,
        COUNT(DISTINCT fm.rut_administradora)                       AS agfs,
        MAX(cn.periodo)                                             AS periodo
    FROM cartera_naci cn
    JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
    CROSS JOIN latest_fm lf
    WHERE cn.periodo = lf.p AND cn.rut_emisor IS NOT NULL
    GROUP BY cn.rut_emisor
)
"""

_FI_EXPOSURE_CTE = """
latest_fi AS (
    SELECT MAX(periodo) AS p FROM cartera_fi_nac
),
fi_exp AS (
    SELECT
        cn.rut_emisor,
        SUM(cn.valorizacion_cierre)                                 AS clp,
        COUNT(DISTINCT cn.run_fondo)                                AS funds,
        COUNT(DISTINCT fi.administrador)                            AS agfs,
        MAX(cn.periodo)                                             AS periodo
    FROM cartera_fi_nac cn
    JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
    CROSS JOIN latest_fi lf
    WHERE cn.periodo = lf.p AND cn.rut_emisor IS NOT NULL
    GROUP BY cn.rut_emisor
)
"""


# ---------------------------------------------------------------------------
# 1. LIST / SEARCH
# ---------------------------------------------------------------------------

@router.get("", response_model=list[EmisorItem])
def list_emisores(
    pagination: Pagination,
    _: CacheHook,
    search: str | None = Query(None, description="Partial company name or exact RUT"),
    fund_type: str | None = Query(None, description="fm | fi | all (default: all)"),
    tipo_instrumento: str | None = Query(None, description="Filter by CMF instrument code, e.g. BC, AC"),
) -> list[EmisorItem]:
    """Rank Chilean companies by total CLP held across FM and FI fund portfolios.

    Default returns all companies sorted by total exposure descending.
    Use ?search= to find a specific company, ?fund_type=fm|fi to narrow scope.
    """
    limit, offset = pagination

    instr_filter_fm = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""
    instr_filter_fi = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""

    params: dict = {"limit": limit, "offset": offset}
    if tipo_instrumento:
        params["tipo_instrumento"] = tipo_instrumento

    search_clause = ""
    if search:
        # Exact RUT match or partial name
        search_clause = "AND (e.rut = :rut OR e.razon_social ILIKE :name)"
        params["rut"] = search.strip()
        params["name"] = f"%{search.strip()}%"

    sql = text(f"""
        WITH
        {_FM_EXPOSURE_CTE},
        {_FI_EXPOSURE_CTE},
        held AS (
            SELECT rut_emisor FROM fm_exp
            UNION
            SELECT rut_emisor FROM fi_exp
        )
        SELECT
            e.rut,
            e.dv,
            e.razon_social,
            COALESCE(fm.clp, 0)                             AS fm_exposure_clp,
            COALESCE(fi.clp, 0)                             AS fi_exposure_clp,
            COALESCE(fm.clp, 0) + COALESCE(fi.clp, 0)      AS total_exposure_clp,
            COALESCE(fm.funds, 0)::int                      AS fm_funds_count,
            COALESCE(fi.funds, 0)::int                      AS fi_funds_count,
            (COALESCE(fm.funds, 0) + COALESCE(fi.funds, 0))::int AS total_funds_count,
            COALESCE(fm.agfs, 0)::int                       AS fm_agfs_count,
            COALESCE(fi.agfs, 0)::int                       AS fi_agfs_count,
            (COALESCE(fm.agfs, 0) + COALESCE(fi.agfs, 0))::int AS total_agfs_count,
            fm.periodo                                      AS latest_fm_period,
            fi.periodo                                      AS latest_fi_period
        FROM emisores e
        JOIN held ON held.rut_emisor = e.rut
        LEFT JOIN fm_exp fm ON fm.rut_emisor = e.rut
        LEFT JOIN fi_exp fi ON fi.rut_emisor = e.rut
        WHERE 1=1 {search_clause}
        ORDER BY total_exposure_clp DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [EmisorItem(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 2. COMPANY DETAIL
# ---------------------------------------------------------------------------

@router.get("/{rut}", response_model=EmisorDetail)
def get_emisor(rut: str, _: CacheHook) -> EmisorDetail:
    """Full profile for a company: exposure, instrument breakdown, top fund holders."""
    with SessionLocal() as session:
        # -- Identity + aggregate exposure --
        summary = session.execute(
            text(f"""
                WITH
                {_FM_EXPOSURE_CTE},
                {_FI_EXPOSURE_CTE}
                SELECT
                    e.rut, e.dv, e.razon_social,
                    COALESCE(fm.clp, 0)                        AS fm_exposure_clp,
                    COALESCE(fi.clp, 0)                        AS fi_exposure_clp,
                    COALESCE(fm.clp, 0) + COALESCE(fi.clp, 0) AS total_exposure_clp,
                    COALESCE(fm.funds, 0)::int                 AS fm_funds_count,
                    COALESCE(fi.funds, 0)::int                 AS fi_funds_count,
                    (COALESCE(fm.funds,0)+COALESCE(fi.funds,0))::int AS total_funds_count,
                    COALESCE(fm.agfs, 0)::int                  AS fm_agfs_count,
                    COALESCE(fi.agfs, 0)::int                  AS fi_agfs_count,
                    (COALESCE(fm.agfs,0)+COALESCE(fi.agfs,0))::int  AS total_agfs_count,
                    fm.periodo                                 AS latest_fm_period,
                    fi.periodo                                 AS latest_fi_period
                FROM emisores e
                LEFT JOIN fm_exp fm ON fm.rut_emisor = e.rut
                LEFT JOIN fi_exp fi ON fi.rut_emisor = e.rut
                WHERE e.rut = :rut
            """),
            {"rut": rut},
        ).mappings().one_or_none()

        if not summary:
            from fastapi import HTTPException
            raise HTTPException(404, "Emisor not found")

        total_clp = float(summary["total_exposure_clp"] or 0)

        # -- Instrument breakdown (FM + FI combined, latest period) --
        instrument_rows = session.execute(
            text("""
                WITH latest_fm AS (SELECT MAX(periodo) AS p FROM cartera_naci),
                     latest_fi AS (SELECT MAX(periodo) AS p FROM cartera_fi_nac),
                fm_instr AS (
                    SELECT cn.tipo_instrumento,
                           SUM(CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN cn.valorizacion_cierre::numeric ELSE 0 END) AS clp,
                           COUNT(DISTINCT cn.run_fondo) AS funds
                    FROM cartera_naci cn
                    CROSS JOIN latest_fm lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                    GROUP BY cn.tipo_instrumento
                ),
                fi_instr AS (
                    SELECT cn.tipo_instrumento,
                           SUM(cn.valorizacion_cierre) AS clp,
                           COUNT(DISTINCT cn.run_fondo) AS funds
                    FROM cartera_fi_nac cn
                    CROSS JOIN latest_fi lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                    GROUP BY cn.tipo_instrumento
                ),
                combined AS (
                    SELECT tipo_instrumento, SUM(clp) AS clp, SUM(funds) AS funds
                    FROM (SELECT * FROM fm_instr UNION ALL SELECT * FROM fi_instr) x
                    GROUP BY tipo_instrumento
                )
                SELECT
                    c.tipo_instrumento,
                    rc.name AS nombre_instrumento,
                    c.clp   AS exposure_clp,
                    c.funds::int AS funds_count,
                    CASE WHEN :total > 0 THEN ROUND((c.clp / :total * 100)::numeric, 2)
                         ELSE NULL END AS pct_of_total
                FROM combined c
                LEFT JOIN ref_codes rc ON rc.domain = 'instrument' AND rc.code = c.tipo_instrumento
                ORDER BY c.clp DESC NULLS LAST
            """),
            {"rut": rut, "total": total_clp},
        ).mappings().all()

        # -- Top 10 fund holders (FM + FI combined) --
        top_funds = session.execute(
            text("""
                WITH latest_fm AS (SELECT MAX(periodo) AS p FROM cartera_naci),
                     latest_fi AS (SELECT MAX(periodo) AS p FROM cartera_fi_nac),
                fm_pos AS (
                    SELECT 'fm' AS fund_type, cn.run_fondo,
                           fm.nombre_fondo, fm.razon_social_administradora AS administrador,
                           CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN cn.valorizacion_cierre::numeric ELSE NULL END AS exposure_clp,
                           CASE WHEN cn.porcentaje_activos_fondo ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN cn.porcentaje_activos_fondo::numeric ELSE NULL END AS pct_activo_fondo,
                           cn.tipo_instrumento, cn.periodo
                    FROM cartera_naci cn
                    JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
                    CROSS JOIN latest_fm lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                ),
                fi_pos AS (
                    SELECT 'fi' AS fund_type, cn.run_fondo,
                           fi.razon_social AS nombre_fondo,
                           fi.administrador,
                           cn.valorizacion_cierre AS exposure_clp,
                           cn.pct_activo_fondo,
                           cn.tipo_instrumento, cn.periodo
                    FROM cartera_fi_nac cn
                    JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
                    CROSS JOIN latest_fi lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                )
                SELECT p.fund_type, p.run_fondo, p.nombre_fondo, p.administrador,
                       p.exposure_clp, p.pct_activo_fondo, p.tipo_instrumento,
                       rc.name AS nombre_instrumento, p.periodo
                FROM (SELECT * FROM fm_pos UNION ALL SELECT * FROM fi_pos) p
                LEFT JOIN ref_codes rc ON rc.domain = 'instrument' AND rc.code = p.tipo_instrumento
                ORDER BY p.exposure_clp DESC NULLS LAST
                LIMIT 10
            """),
            {"rut": rut},
        ).mappings().all()

    return EmisorDetail(
        **dict(summary),
        instrument_breakdown=[InstrumentBreakdown(**dict(r)) for r in instrument_rows],
        top_funds=[TopFundHolding(**dict(r)) for r in top_funds],
    )


# ---------------------------------------------------------------------------
# 3. ALL FUND POSITIONS (latest period)
# ---------------------------------------------------------------------------

@router.get("/{rut}/funds", response_model=list[FundHolding])
def get_emisor_funds(
    rut: str,
    pagination: Pagination,
    _: CacheHook,
    fund_type: str | None = Query(None, description="fm | fi (default: both)"),
    tipo_instrumento: str | None = Query(None),
) -> list[FundHolding]:
    """Every fund currently holding this company, sorted by CLP exposure descending."""
    limit, offset = pagination
    params: dict = {"rut": rut, "limit": limit, "offset": offset}

    instr_fm = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""
    instr_fi = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""
    if tipo_instrumento:
        params["tipo_instrumento"] = tipo_instrumento

    include_fm = fund_type in (None, "fm", "all")
    include_fi = fund_type in (None, "fi", "all")

    parts = []
    if include_fm:
        parts.append(f"""
            SELECT 'fm' AS fund_type, cn.run_fondo,
                   fm.nombre_fondo, fm.razon_social_administradora AS administrador,
                   CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN cn.valorizacion_cierre::numeric ELSE NULL END AS exposure_clp,
                   CASE WHEN cn.porcentaje_activos_fondo ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN cn.porcentaje_activos_fondo::numeric ELSE NULL END AS pct_activo_fondo,
                   cn.tipo_instrumento, cn.periodo
            FROM cartera_naci cn
            JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              AND cn.periodo = (SELECT MAX(periodo) FROM cartera_naci)
              {instr_fm}
        """)
    if include_fi:
        parts.append(f"""
            SELECT 'fi' AS fund_type, cn.run_fondo,
                   fi.razon_social AS nombre_fondo,
                   fi.administrador,
                   cn.valorizacion_cierre AS exposure_clp,
                   cn.pct_activo_fondo,
                   cn.tipo_instrumento, cn.periodo
            FROM cartera_fi_nac cn
            JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              AND cn.periodo = (SELECT MAX(periodo) FROM cartera_fi_nac)
              {instr_fi}
        """)

    if not parts:
        return []

    union_sql = " UNION ALL ".join(parts)
    sql = text(f"""
        SELECT p.fund_type, p.run_fondo, p.nombre_fondo, p.administrador,
               p.exposure_clp, p.pct_activo_fondo, p.tipo_instrumento,
               rc.name AS nombre_instrumento, p.periodo
        FROM ({union_sql}) p
        LEFT JOIN ref_codes rc ON rc.domain = 'instrument' AND rc.code = p.tipo_instrumento
        ORDER BY p.exposure_clp DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundHolding(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# RAW POSITIONS — full instrument-level detail
# ---------------------------------------------------------------------------

@router.get("/{rut}/positions", response_model=list[EmisorPosition])
def get_emisor_positions(
    rut: str,
    pagination: Pagination,
    _: CacheHook,
    fund_type: str | None = Query(None, description="fm | fi (default: both)"),
    tipo_instrumento: str | None = Query(None, description="Filter by instrument code, e.g. ACC, BC, EC"),
    period: str | None = Query(None, description="YYYY-MM-DD — defaults to latest available period"),
) -> list[EmisorPosition]:
    """Every individual portfolio position for this company across all funds.

    Returns one row per fund × instrument line — not aggregated. This gives
    the frontend everything needed to render instrument-specific detail cards:
    - Bonds (BC, BE, BB…): tir, fecha_vencimiento, tipo_interes, porcentaje_valor_par
    - Stocks (ACC, ACCR, ACE): cantidad_unidades, porcentaje_capital_emisor
    - Commercial paper (EC): tir, fecha_vencimiento
    - Deposits (DPC, DPL): tir, fecha_vencimiento
    """
    limit, offset = pagination
    params: dict = {"rut": rut, "limit": limit, "offset": offset}
    instr_filter = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""
    if tipo_instrumento:
        params["tipo_instrumento"] = tipo_instrumento

    include_fm = fund_type in (None, "fm", "all")
    include_fi = fund_type in (None, "fi", "all")

    parts = []

    if include_fm:
        if period:
            from src.api.mutual_funds import _first_of_month
            fm_period_clause = "AND cn.periodo = :fm_period"
            params["fm_period"] = _first_of_month(period)
        else:
            fm_period_clause = "AND cn.periodo = (SELECT MAX(periodo) FROM cartera_naci)"

        parts.append(f"""
            SELECT
                'fm'                                    AS fund_type,
                cn.run_fondo,
                fm.nombre_fondo,
                fm.razon_social_administradora          AS administrador,
                cn.periodo,
                cn.tipo_instrumento,
                cn.nemotecnico,
                cn.situacion_instrumento,
                cn.clasificacion_riesgo,
                CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.valorizacion_cierre::numeric ELSE NULL END     AS valorizacion_cierre,
                cn.moneda_liquidacion,
                CASE WHEN cn.porcentaje_activos_fondo ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.porcentaje_activos_fondo::numeric ELSE NULL END AS pct_activo_fondo,
                CASE WHEN cn.tir ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.tir::numeric ELSE NULL END                     AS tir,
                cn.fecha_vencimiento,
                cn.tipo_interes,
                cn.base_tasa,
                CASE WHEN cn.porcentaje_valor_par ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.porcentaje_valor_par::numeric ELSE NULL END    AS porcentaje_valor_par,
                CASE WHEN cn.cantidad_unidades ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.cantidad_unidades::numeric ELSE NULL END       AS cantidad_unidades,
                cn.tipo_unidades,
                CASE WHEN cn.porcentaje_capital_emisor ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.porcentaje_capital_emisor::numeric ELSE NULL END AS porcentaje_capital_emisor,
                CASE WHEN cn.porcentaje_activos_emisor ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.porcentaje_activos_emisor::numeric ELSE NULL END AS porcentaje_activos_emisor
            FROM cartera_naci cn
            JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              {fm_period_clause}
              {instr_filter}
        """)

    if include_fi:
        if period:
            from datetime import date as _date
            p = _date.fromisoformat(period)
            fi_period_clause = "AND cn.periodo = :fi_period"
            params["fi_period"] = _date(p.year, p.month, 1)
        else:
            fi_period_clause = "AND cn.periodo = (SELECT MAX(periodo) FROM cartera_fi_nac)"

        parts.append(f"""
            SELECT
                'fi'                        AS fund_type,
                cn.run_fondo,
                fi.razon_social             AS nombre_fondo,
                fi.administrador,
                cn.periodo,
                cn.tipo_instrumento,
                cn.nemotecnico,
                cn.situacion_instrumento,
                cn.clasif_riesgo            AS clasificacion_riesgo,
                cn.valorizacion_cierre,
                cn.cod_moneda_liquidacion   AS moneda_liquidacion,
                cn.pct_activo_fondo,
                cn.tir_val_par_precio       AS tir,
                cn.fecha_vencimiento,
                cn.tipo_interes,
                cn.base_tasa,
                NULL::numeric               AS porcentaje_valor_par,
                cn.cant_unidades            AS cantidad_unidades,
                cn.tipo_unidades,
                cn.pct_capital_emisor       AS porcentaje_capital_emisor,
                cn.pct_activo_emisor        AS porcentaje_activos_emisor
            FROM cartera_fi_nac cn
            JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              {fi_period_clause}
              {instr_filter}
        """)

    if not parts:
        return []

    union_sql = " UNION ALL ".join(parts)
    sql = text(f"""
        SELECT p.*,
               rc.name AS nombre_instrumento
        FROM ({union_sql}) p
        LEFT JOIN ref_codes rc ON rc.domain = 'instrument' AND rc.code = p.tipo_instrumento
        ORDER BY p.valorizacion_cierre DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [EmisorPosition(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 4. EXPOSURE HISTORY (the CEO chart)
# ---------------------------------------------------------------------------

@router.get("/{rut}/history", response_model=list[HistoryPoint])
def get_emisor_history(
    rut: str,
    _: CacheHook,
    fund_type: str | None = Query(None, description="fm | fi (default: both)"),
    from_date: date | None = Query(None, description="Start period, defaults to 2020-01-01"),
    tipo_instrumento: str | None = Query(None, description="Filter to a single instrument type, e.g. BC, ACC, EC"),
) -> list[HistoryPoint]:
    """Monthly (FM) and quarterly (FI) time series of total market exposure to this company.

    Pass ?tipo_instrumento=BC to drill down into a single instrument type —
    powers the timeline chart in the instrument drill-down view.
    """
    effective_from = from_date or date(2020, 1, 1)
    params: dict = {"rut": rut, "from_date": effective_from}

    instr_filter = "AND cn.tipo_instrumento = :tipo_instrumento" if tipo_instrumento else ""
    if tipo_instrumento:
        params["tipo_instrumento"] = tipo_instrumento

    include_fm = fund_type in (None, "fm", "all")
    include_fi = fund_type in (None, "fi", "all")

    parts = []
    if include_fm:
        parts.append(f"""
            SELECT
                cn.periodo,
                'fm'                                                           AS fund_type,
                SUM(CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                         THEN cn.valorizacion_cierre::numeric ELSE 0 END)      AS exposure_clp,
                COUNT(DISTINCT cn.run_fondo)::int                              AS funds_count,
                COUNT(DISTINCT fm.rut_administradora)::int                     AS agfs_count
            FROM cartera_naci cn
            JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut AND cn.periodo >= :from_date
              {instr_filter}
            GROUP BY cn.periodo
        """)
    if include_fi:
        parts.append(f"""
            SELECT
                cn.periodo,
                'fi'                                        AS fund_type,
                SUM(cn.valorizacion_cierre)                 AS exposure_clp,
                COUNT(DISTINCT cn.run_fondo)::int           AS funds_count,
                COUNT(DISTINCT fi.administrador)::int       AS agfs_count
            FROM cartera_fi_nac cn
            JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut AND cn.periodo >= :from_date
              {instr_filter}
            GROUP BY cn.periodo
        """)

    if not parts:
        return []

    union_sql = " UNION ALL ".join(parts)
    sql = text(f"SELECT * FROM ({union_sql}) x ORDER BY periodo ASC, fund_type")

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [HistoryPoint(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 5. FUND-SPECIFIC EMISOR HISTORY
# ---------------------------------------------------------------------------

@router.get("/{rut}/funds/{run_fondo}/history", response_model=list[FundEmisorHistoryPoint])
def get_emisor_fund_history(
    rut: str,
    run_fondo: str,
    _: CacheHook,
    fund_type: str | None = Query(None, description="fm | fi — auto-detected if omitted"),
    from_date: date | None = Query(None, description="Start period, defaults to 2020-01-01"),
) -> list[FundEmisorHistoryPoint]:
    """Time series of how much a specific fund has held in a specific company.

    Each row is one portfolio period (monthly for FM, quarterly for FI).
    Multiple rows per period can appear when the fund holds the company
    across different instrument types simultaneously.
    """
    effective_from = from_date or date(2020, 1, 1)
    params: dict = {"rut": rut, "run_fondo": run_fondo, "from_date": effective_from}

    include_fm = fund_type in (None, "fm", "all")
    include_fi = fund_type in (None, "fi", "all")

    parts = []
    if include_fm:
        parts.append("""
            SELECT
                cn.periodo,
                'fm'                        AS fund_type,
                cn.run_fondo,
                fm.nombre_fondo,
                fm.razon_social_administradora AS administrador,
                CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.valorizacion_cierre::numeric ELSE NULL END AS exposure_clp,
                CASE WHEN cn.porcentaje_activos_fondo ~ '^-?[0-9]+(\\.[0-9]+)?$'
                     THEN cn.porcentaje_activos_fondo::numeric ELSE NULL END AS pct_activo_fondo,
                cn.tipo_instrumento
            FROM cartera_naci cn
            JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              AND cn.run_fondo  = :run_fondo
              AND cn.periodo   >= :from_date
        """)
    if include_fi:
        parts.append("""
            SELECT
                cn.periodo,
                'fi'                AS fund_type,
                cn.run_fondo,
                fi.razon_social     AS nombre_fondo,
                fi.administrador,
                cn.valorizacion_cierre AS exposure_clp,
                cn.pct_activo_fondo,
                cn.tipo_instrumento
            FROM cartera_fi_nac cn
            JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
            WHERE cn.rut_emisor = :rut
              AND cn.run_fondo  = :run_fondo
              AND cn.periodo   >= :from_date
        """)

    if not parts:
        return []

    union_sql = " UNION ALL ".join(parts)
    sql = text(f"""
        SELECT p.periodo, p.fund_type, p.run_fondo, p.nombre_fondo, p.administrador,
               p.exposure_clp, p.pct_activo_fondo, p.tipo_instrumento,
               rc.name AS nombre_instrumento
        FROM ({union_sql}) p
        LEFT JOIN ref_codes rc ON rc.domain = 'instrument' AND rc.code = p.tipo_instrumento
        ORDER BY p.periodo ASC, p.exposure_clp DESC NULLS LAST
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundEmisorHistoryPoint(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 6. CONCENTRATION (Herfindahl index by AGF)
# ---------------------------------------------------------------------------

@router.get("/{rut}/concentration", response_model=ConcentrationResult)
def get_emisor_concentration(rut: str, _: CacheHook) -> ConcentrationResult:
    """How concentrated is this company's exposure across administradoras?

    Returns a Herfindahl-Hirschman Index (0-10000). Below 1500 = diversified,
    1500-2500 = moderate, above 2500 = highly concentrated in few AGFs.
    """
    with SessionLocal() as session:
        identity = session.execute(
            text("SELECT rut, dv, razon_social FROM emisores WHERE rut = :rut"),
            {"rut": rut},
        ).mappings().one_or_none()

        if not identity:
            from fastapi import HTTPException
            raise HTTPException(404, "Emisor not found")

        agf_rows = session.execute(
            text("""
                WITH latest_fm AS (SELECT MAX(periodo) AS p FROM cartera_naci),
                     latest_fi AS (SELECT MAX(periodo) AS p FROM cartera_fi_nac),
                fm_agf AS (
                    SELECT fm.rut_administradora AS rut_admin,
                           fm.razon_social_administradora AS administrador,
                           'fm' AS fund_type,
                           SUM(CASE WHEN cn.valorizacion_cierre ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN cn.valorizacion_cierre::numeric ELSE 0 END) AS clp,
                           COUNT(DISTINCT cn.run_fondo)::int AS funds_count
                    FROM cartera_naci cn
                    JOIN fondo_mutuo fm ON fm.run_fondo = cn.run_fondo
                    CROSS JOIN latest_fm lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                    GROUP BY fm.rut_administradora, fm.razon_social_administradora
                ),
                fi_agf AS (
                    SELECT NULL AS rut_admin,
                           fi.administrador,
                           'fi' AS fund_type,
                           SUM(cn.valorizacion_cierre) AS clp,
                           COUNT(DISTINCT cn.run_fondo)::int AS funds_count
                    FROM cartera_fi_nac cn
                    JOIN fondos_inversion fi ON fi.run_fondo = cn.run_fondo
                    CROSS JOIN latest_fi lf
                    WHERE cn.periodo = lf.p AND cn.rut_emisor = :rut
                    GROUP BY fi.administrador
                ),
                all_agf AS (
                    SELECT rut_admin, administrador, fund_type, clp, funds_count
                    FROM fm_agf
                    UNION ALL
                    SELECT rut_admin, administrador, fund_type, clp, funds_count
                    FROM fi_agf
                ),
                total AS (SELECT SUM(clp) AS t FROM all_agf)
                SELECT
                    a.administrador,
                    a.rut_admin AS rut_administradora,
                    a.fund_type,
                    a.clp AS exposure_clp,
                    CASE WHEN t.t > 0 THEN ROUND((a.clp / t.t * 100)::numeric, 2) ELSE 0 END AS pct_of_total,
                    a.funds_count,
                    t.t AS total_clp
                FROM all_agf a
                CROSS JOIN total t
                ORDER BY a.clp DESC NULLS LAST
            """),
            {"rut": rut},
        ).mappings().all()

    if not agf_rows:
        from fastapi import HTTPException
        raise HTTPException(404, "No portfolio exposure found for this emisor")

    total_clp = float(agf_rows[0]["total_clp"] or 0)

    # HHI = sum of squared market shares (in %)
    hhi: float | None = None
    if total_clp > 0:
        hhi = round(sum((float(r["pct_of_total"]) ** 2) for r in agf_rows), 2)

    if hhi is None:
        label = "unknown"
    elif hhi < 1500:
        label = "diversified"
    elif hhi < 2500:
        label = "moderate"
    else:
        label = "concentrated"

    return ConcentrationResult(
        rut=identity["rut"],
        razon_social=identity["razon_social"],
        total_exposure_clp=total_clp,
        herfindahl_index=hhi,
        concentration_label=label,
        agfs=[
            ConcentrationAGF(
                administrador=r["administrador"],
                rut_administradora=r["rut_administradora"],
                fund_type=r["fund_type"],
                exposure_clp=float(r["exposure_clp"] or 0),
                pct_of_total=float(r["pct_of_total"] or 0),
                funds_count=r["funds_count"],
            )
            for r in agf_rows
        ],
    )
