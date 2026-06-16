from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/shareholders", tags=["shareholders"])

# AUM for a fund/period = median of (valorizacion_cierre * 100 / pct_activo_fondo)
# across all positions in cartera_fi_nac + cartera_fi_ext.
# Median is more robust than average — tiny positions with small pct_activo_fondo
# would blow up the average estimate.
_FUND_AUM_CTE = """
fund_aum AS (
    SELECT c.run_fondo, c.periodo,
           PERCENTILE_CONT(0.5) WITHIN GROUP (
               ORDER BY c.valorizacion_cierre * 100.0 / NULLIF(c.pct_activo_fondo, 0)
           ) AS aum_clp
    FROM (
        SELECT run_fondo, periodo, valorizacion_cierre, pct_activo_fondo
        FROM cartera_fi_nac
        UNION ALL
        SELECT run_fondo, periodo, valorizacion_cierre, pct_activo_fondo
        FROM cartera_fi_ext
    ) c
    WHERE c.valorizacion_cierre IS NOT NULL
      AND c.pct_activo_fondo > 0.01
      {extra_filter}
    GROUP BY c.run_fondo, c.periodo
)
"""


# ── Schemas ────────────────────────────────────────────────────────────────────

class ShareholderInFund(BaseModel):
    periodo: date
    rank: int
    rut: str | None
    nombre: str | None
    tipo_persona: str | None
    pct_propiedad: float | None
    aum_clp: float | None
    fund_aum_clp: float | None


class FundPosition(BaseModel):
    periodo: date
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    rank: int
    pct_propiedad: float | None
    aum_clp: float | None
    fund_aum_clp: float | None


class AdminShareholder(BaseModel):
    rut: str | None
    nombre: str | None
    tipo_persona: str | None
    num_funds: int
    total_aum_clp: float | None
    avg_pct_propiedad: float | None


class CompareHolder(BaseModel):
    rut: str
    nombre: str | None
    tipo_persona: str | None
    aum_a: float | None
    aum_b: float | None
    merged_aum: float
    segment: str  # 'shared' | 'only_a' | 'only_b'


class CompareSummary(BaseModel):
    admin_a: str
    admin_b: str
    period_a: date | None
    period_b: date | None
    total_aum_a: float
    total_aum_b: float
    shareholder_count_a: int
    shareholder_count_b: int
    shared_count: int
    only_a_count: int
    only_b_count: int
    merged_aum: float
    merged_shareholder_count: int


class CompareResponse(BaseModel):
    summary: CompareSummary
    shareholders: list[CompareHolder]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/fund/{run}", response_model=list[ShareholderInFund])
def fund_shareholders(
    run: str,
    pagination: Pagination,
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date, e.g. 2025-12-31. Defaults to latest."),
) -> list[ShareholderInFund]:
    """Shareholders of a fund across time — or for a specific quarter."""
    limit, offset = pagination
    aum_cte = _FUND_AUM_CTE.format(extra_filter="AND c.run_fondo = :run")

    period_filter = "AND a.periodo = :period" if period else ""
    params: dict = {"run": run, "limit": limit, "offset": offset}
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte}
        SELECT
            a.periodo,
            a.rank,
            a.rut,
            COALESCE(a.nombre_canonical, a.nombre) AS nombre,
            a.tipo_persona,
            a.pct_propiedad,
            ROUND((a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS aum_clp,
            ROUND(fa.aum_clp::numeric, 0)                              AS fund_aum_clp
        FROM aportantes_fi a
        LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
        WHERE a.run_fondo = :run
          {period_filter}
        ORDER BY a.periodo DESC, a.rank
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [ShareholderInFund(**dict(r)) for r in rows]


@router.get("/entity/{rut}", response_model=list[FundPosition])
def entity_positions(
    rut: str,
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Filter by administrador name"),
    period: date | None = Query(None),
) -> list[FundPosition]:
    """All fund positions held by a specific shareholder across time."""
    limit, offset = pagination
    aum_cte = _FUND_AUM_CTE.format(extra_filter="")

    conditions = ["a.rut = :rut"]
    params: dict = {"rut": rut, "limit": limit, "offset": offset}

    if admin:
        conditions.append("f.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if period:
        conditions.append("a.periodo = :period")
        params["period"] = period

    where = "WHERE " + " AND ".join(conditions)
    sql = text(f"""
        WITH {aum_cte}
        SELECT
            a.periodo,
            a.run_fondo,
            f.razon_social,
            f.administrador,
            a.rank,
            a.pct_propiedad,
            ROUND((a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS aum_clp,
            ROUND(fa.aum_clp::numeric, 0)                              AS fund_aum_clp
        FROM aportantes_fi a
        LEFT JOIN fondos_inversion f  ON f.run_fondo = a.run_fondo
        LEFT JOIN fund_aum fa         ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
        {where}
        ORDER BY a.periodo DESC, aum_clp DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundPosition(**dict(r)) for r in rows]


@router.get("/admin", response_model=list[AdminShareholder])
def admin_shareholders(
    pagination: Pagination,
    _: CacheHook,
    admin: str = Query(..., description="Administrador name (partial match)"),
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest available."),
) -> list[AdminShareholder]:
    """Top shareholders aggregated across all funds of an administrador."""
    limit, offset = pagination
    aum_cte = _FUND_AUM_CTE.format(
        extra_filter="""
        AND c.run_fondo IN (
            SELECT run_fondo FROM fondos_inversion WHERE administrador ILIKE :admin
        )"""
    )

    period_clause = (
        "AND a.periodo = :period"
        if period
        else "AND a.periodo = (SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin)"
    )
    params: dict = {"admin": f"%{admin}%", "limit": limit, "offset": offset}
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte}
        SELECT
            a.rut,
            COALESCE(a.nombre_canonical, a.nombre)         AS nombre,
            a.tipo_persona,
            COUNT(DISTINCT a.run_fondo)                    AS num_funds,
            ROUND(
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0
            )                                              AS total_aum_clp,
            ROUND(AVG(a.pct_propiedad)::numeric, 4)        AS avg_pct_propiedad
        FROM aportantes_fi a
        JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
        LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
        WHERE f.administrador ILIKE :admin
          AND a.rut IS NOT NULL
          {period_clause}
        GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        ORDER BY total_aum_clp DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [AdminShareholder(**dict(r)) for r in rows]


@router.get("/compare", response_model=CompareResponse)
def compare_admins(
    _: CacheHook,
    admin_a: str = Query(..., description="First administrador (partial match)"),
    admin_b: str = Query(..., description="Second administrador (partial match)"),
    period: date | None = Query(None, description="Quarter-end date. Defaults to each admin's latest."),
) -> CompareResponse:
    """
    Side-by-side shareholder comparison between two administradores.
    Returns shared shareholders, exclusive to each, and a merge summary.
    """
    aum_cte = _FUND_AUM_CTE.format(
        extra_filter="""
        AND c.run_fondo IN (
            SELECT run_fondo FROM fondos_inversion
            WHERE administrador ILIKE :admin_a OR administrador ILIKE :admin_b
        )"""
    )

    period_a_expr = ":period" if period else (
        "(SELECT MAX(x.periodo) FROM aportantes_fi x "
        "JOIN fondos_inversion g ON g.run_fondo = x.run_fondo "
        "WHERE g.administrador ILIKE :admin_a)"
    )
    period_b_expr = ":period" if period else (
        "(SELECT MAX(x.periodo) FROM aportantes_fi x "
        "JOIN fondos_inversion g ON g.run_fondo = x.run_fondo "
        "WHERE g.administrador ILIKE :admin_b)"
    )

    params: dict = {
        "admin_a": f"%{admin_a}%",
        "admin_b": f"%{admin_b}%",
    }
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte},
        holders_a AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                a.tipo_persona,
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE f.administrador ILIKE :admin_a
              AND a.rut IS NOT NULL
              AND a.periodo = {period_a_expr}
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        ),
        holders_b AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                a.tipo_persona,
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE f.administrador ILIKE :admin_b
              AND a.rut IS NOT NULL
              AND a.periodo = {period_b_expr}
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        ),
        combined AS (
            SELECT
                COALESCE(a.rut,          b.rut)         AS rut,
                COALESCE(a.nombre,       b.nombre)       AS nombre,
                COALESCE(a.tipo_persona, b.tipo_persona) AS tipo_persona,
                a.aum                                    AS aum_a,
                b.aum                                    AS aum_b,
                COALESCE(a.aum, 0) + COALESCE(b.aum, 0) AS merged_aum,
                CASE
                    WHEN a.rut IS NOT NULL AND b.rut IS NOT NULL THEN 'shared'
                    WHEN a.rut IS NOT NULL                        THEN 'only_a'
                    ELSE                                               'only_b'
                END AS segment
            FROM holders_a a
            FULL OUTER JOIN holders_b b ON a.rut = b.rut
        )
        SELECT
            -- summary aggregates
            (SELECT COALESCE(SUM(aum), 0) FROM holders_a)          AS total_aum_a,
            (SELECT COALESCE(SUM(aum), 0) FROM holders_b)          AS total_aum_b,
            (SELECT COUNT(*) FROM holders_a)                        AS shareholder_count_a,
            (SELECT COUNT(*) FROM holders_b)                        AS shareholder_count_b,
            (SELECT COUNT(*) FROM combined WHERE segment = 'shared') AS shared_count,
            (SELECT COUNT(*) FROM combined WHERE segment = 'only_a') AS only_a_count,
            (SELECT COUNT(*) FROM combined WHERE segment = 'only_b') AS only_b_count,
            (SELECT COALESCE(SUM(merged_aum), 0) FROM combined)     AS merged_aum,
            (SELECT COUNT(*) FROM combined)                         AS merged_shareholder_count,
            (SELECT {period_a_expr})                                AS period_a,
            (SELECT {period_b_expr})                                AS period_b,
            -- row-level data
            c.rut, c.nombre, c.tipo_persona,
            c.aum_a, c.aum_b, c.merged_aum, c.segment
        FROM combined c
        ORDER BY c.merged_aum DESC NULLS LAST
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    if not rows:
        raise HTTPException(404, "No shareholder data found for the given administrators")

    first = rows[0]
    summary = CompareSummary(
        admin_a=admin_a,
        admin_b=admin_b,
        period_a=first["period_a"],
        period_b=first["period_b"],
        total_aum_a=float(first["total_aum_a"] or 0),
        total_aum_b=float(first["total_aum_b"] or 0),
        shareholder_count_a=int(first["shareholder_count_a"]),
        shareholder_count_b=int(first["shareholder_count_b"]),
        shared_count=int(first["shared_count"]),
        only_a_count=int(first["only_a_count"]),
        only_b_count=int(first["only_b_count"]),
        merged_aum=float(first["merged_aum"] or 0),
        merged_shareholder_count=int(first["merged_shareholder_count"]),
    )
    shareholders = [
        CompareHolder(
            rut=r["rut"],
            nombre=r["nombre"],
            tipo_persona=r["tipo_persona"],
            aum_a=float(r["aum_a"]) if r["aum_a"] is not None else None,
            aum_b=float(r["aum_b"]) if r["aum_b"] is not None else None,
            merged_aum=float(r["merged_aum"] or 0),
            segment=r["segment"],
        )
        for r in rows
    ]
    return CompareResponse(summary=summary, shareholders=shareholders)


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Schemas ────────────────────────────────────────────────────────────────────

class TopShareholder(BaseModel):
    rut: str | None
    nombre: str | None
    tipo_persona: str | None
    funds_count: int
    agfs_count: int
    total_aum_clp: float | None
    periodo: date


class AGFShare(BaseModel):
    administrador: str | None
    aum_clp: float
    pct_of_wallet: float
    funds_count: int


class InvestorHolding(BaseModel):
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    periodo: date
    rank: int
    pct_propiedad: float | None
    aum_clp: float | None
    fund_aum_clp: float | None


class InvestorProfile(BaseModel):
    rut: str
    nombre: str | None
    tipo_persona: str | None
    total_aum_clp: float | None
    funds_count: int
    agfs_count: int
    periodo: date
    agf_breakdown: list[AGFShare]
    top_holdings: list[InvestorHolding]


class EvolutionPoint(BaseModel):
    periodo: date
    total_aum_clp: float | None
    funds_count: int
    agfs_count: int


class ConcentrationPeriod(BaseModel):
    periodo: date
    fund_aum_clp: float | None
    shareholders_reported: int
    top1_pct: float | None
    top3_pct: float | None
    top5_pct: float | None
    top10_pct: float | None
    top1_aum_clp: float | None
    top3_aum_clp: float | None
    top5_aum_clp: float | None
    herfindahl_index: float | None
    concentration_label: str


class RetentionEntry(BaseModel):
    rut: str | None
    nombre: str | None
    tipo_persona: str | None
    current_aum_clp: float | None
    prev_aum_clp: float | None
    delta_clp: float | None
    delta_pct: float | None
    status: str  # new | growing | stable | shrinking | exiting
    funds_current: int
    funds_prev: int
    current_period: date | None
    prev_period: date | None


class MatrixAGFRow(BaseModel):
    administrador: str | None
    aum_clp: float
    pct_of_wallet: float
    funds_count: int


class MatrixInvestor(BaseModel):
    rut: str
    nombre: str | None
    total_aum_clp: float
    agfs_count: int
    btg_aum_clp: float
    btg_wallet_pct: float
    rows: list[MatrixAGFRow]


class MatrixResponse(BaseModel):
    periodo: date
    investors: list[MatrixInvestor]


class OpportunityEntry(BaseModel):
    rut: str
    nombre: str | None
    total_aum_clp: float
    btg_aum_clp: float
    btg_wallet_pct: float
    agfs_count: int
    main_agf: str | None
    main_agf_wallet_pct: float
    opportunity_aum_clp: float
    score: float
    reason: str


class FlowEntry(BaseModel):
    rut: str
    nombre: str | None
    current_aum_clp: float | None
    prev_aum_clp: float | None
    delta_clp: float | None
    delta_pct: float | None
    status: str
    current_agfs_count: int
    prev_agfs_count: int
    current_period: date | None
    prev_period: date | None


class LargestHolder(BaseModel):
    rut: str | None
    nombre: str | None
    pct_propiedad: float | None
    aum_clp: float | None


class FundConcentrationRankingEntry(BaseModel):
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    periodo: date
    fund_aum_clp: float | None
    shareholders_reported: int
    top1_pct: float | None
    top3_pct: float | None
    top5_pct: float | None
    top10_pct: float | None
    herfindahl_index: float | None
    concentration_label: str
    largest_holder: LargestHolder


class MergeSimulationShareholder(BaseModel):
    rut: str
    nombre: str | None
    aum_a: float | None
    aum_b: float | None
    merged_aum: float
    wallet_share_a: float | None
    wallet_share_b: float | None
    market_total_aum_clp: float
    concentration_after_pct: float


class MergeSimulationSummary(BaseModel):
    admin_a: str
    admin_b: str
    merged_aum_clp: float
    overlap_aum_clp: float
    overlap_pct_of_merged: float
    shared_shareholders: int
    exclusive_shareholders: int
    concentration_hhi_before_a: float
    concentration_hhi_before_b: float
    concentration_hhi_after: float
    top10_after_pct: float
    cross_sell_opportunity_clp: float
    retention_risk_clp: float


class MergeSimulationSegments(BaseModel):
    shared: list[MergeSimulationShareholder]
    only_a: list[MergeSimulationShareholder]
    only_b: list[MergeSimulationShareholder]
    high_risk_overlap: list[MergeSimulationShareholder]
    cross_sell_targets: list[MergeSimulationShareholder]


class MergeSimulationResponse(BaseModel):
    summary: MergeSimulationSummary
    segments: MergeSimulationSegments


# ── 1. Market whales leaderboard ───────────────────────────────────────────────

@router.get("/top", response_model=list[TopShareholder])
def top_shareholders(
    pagination: Pagination,
    _: CacheHook,
    tipo_persona: str | None = Query(None, description="J=juridica, N=natural"),
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
) -> list[TopShareholder]:
    """Largest institutional investors in Chile by total FI AUM — ranked across all funds.

    This is the full market leaderboard: who holds the most capital across all
    investment funds, which AGFs they use, and how many funds they sit in.
    """
    limit, offset = pagination
    aum_cte = _FUND_AUM_CTE.format(extra_filter="")

    period_clause = "AND a.periodo = :period" if period else \
        "AND a.periodo = (SELECT MAX(periodo) FROM aportantes_fi)"
    tipo_clause = "AND a.tipo_persona = :tipo_persona" if tipo_persona else ""

    params: dict = {"limit": limit, "offset": offset}
    if period:
        params["period"] = period
    if tipo_persona:
        params["tipo_persona"] = tipo_persona

    sql = text(f"""
        WITH {aum_cte}
        SELECT
            a.rut,
            COALESCE(a.nombre_canonical, a.nombre)  AS nombre,
            a.tipo_persona,
            COUNT(DISTINCT a.run_fondo)::int         AS funds_count,
            COUNT(DISTINCT f.administrador)::int     AS agfs_count,
            ROUND(SUM(a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS total_aum_clp,
            MAX(a.periodo)                           AS periodo
        FROM aportantes_fi a
        JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
        LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
        WHERE a.rut IS NOT NULL
          {period_clause}
          {tipo_clause}
        GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        ORDER BY total_aum_clp DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [TopShareholder(**dict(r)) for r in rows]


# ── 2. Full investor profile ───────────────────────────────────────────────────

@router.get("/entity/{rut}/profile", response_model=InvestorProfile)
def investor_profile(
    rut: str,
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
) -> InvestorProfile:
    """Complete dossier for an institutional investor.

    Answers: How much do they have in total? How many funds? Which AGFs are they
    using and how much wallet share does each have? What are their top holdings?
    """
    aum_cte = _FUND_AUM_CTE.format(extra_filter="")
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi WHERE rut = :rut)"
    params: dict = {"rut": rut}
    if period:
        params["period"] = period

    with SessionLocal() as session:
        # Summary + AGF breakdown
        summary_row = session.execute(
            text(f"""
                WITH {aum_cte}
                SELECT
                    a.rut,
                    COALESCE(a.nombre_canonical, a.nombre)  AS nombre,
                    a.tipo_persona,
                    COUNT(DISTINCT a.run_fondo)::int         AS funds_count,
                    COUNT(DISTINCT f.administrador)::int     AS agfs_count,
                    ROUND(SUM(a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS total_aum_clp,
                    MAX(a.periodo)                           AS periodo
                FROM aportantes_fi a
                JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
                LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
                WHERE a.rut = :rut AND a.periodo = {period_expr}
                GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
            """),
            params,
        ).mappings().one_or_none()

        if not summary_row:
            raise HTTPException(404, "Shareholder not found")

        total_aum = float(summary_row["total_aum_clp"] or 0)
        effective_period = summary_row["periodo"]

        # AGF wallet breakdown
        agf_rows = session.execute(
            text(f"""
                WITH {aum_cte}
                SELECT
                    f.administrador,
                    ROUND(SUM(a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS aum_clp,
                    COUNT(DISTINCT a.run_fondo)::int AS funds_count
                FROM aportantes_fi a
                JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
                LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
                WHERE a.rut = :rut AND a.periodo = {period_expr}
                GROUP BY f.administrador
                ORDER BY aum_clp DESC NULLS LAST
            """),
            params,
        ).mappings().all()

        # Top holdings
        holding_rows = session.execute(
            text(f"""
                WITH {aum_cte}
                SELECT
                    a.run_fondo, f.razon_social, f.administrador,
                    a.periodo, a.rank, a.pct_propiedad,
                    ROUND((a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS aum_clp,
                    ROUND(fa.aum_clp::numeric, 0)                              AS fund_aum_clp
                FROM aportantes_fi a
                JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
                LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
                WHERE a.rut = :rut AND a.periodo = {period_expr}
                ORDER BY aum_clp DESC NULLS LAST
                LIMIT 10
            """),
            params,
        ).mappings().all()

    agf_breakdown = [
        AGFShare(
            administrador=r["administrador"],
            aum_clp=float(r["aum_clp"] or 0),
            pct_of_wallet=round(float(r["aum_clp"] or 0) / total_aum * 100, 2) if total_aum else 0,
            funds_count=r["funds_count"],
        )
        for r in agf_rows
    ]

    return InvestorProfile(
        **dict(summary_row),
        agf_breakdown=agf_breakdown,
        top_holdings=[InvestorHolding(**dict(r)) for r in holding_rows],
    )


# ── 3. Quarterly AUM evolution ─────────────────────────────────────────────────

@router.get("/entity/{rut}/evolution", response_model=list[EvolutionPoint])
def investor_evolution(
    rut: str,
    _: CacheHook,
    from_date: date | None = Query(None, description="Start quarter. Defaults to 2020-01-01."),
) -> list[EvolutionPoint]:
    """Quarter-by-quarter AUM, fund count, and AGF count for an investor.

    The key chart for answering: is this investor growing their FI exposure,
    rotating between funds, or quietly reducing their position in the market?
    """
    aum_cte = _FUND_AUM_CTE.format(extra_filter="")
    effective_from = from_date or date(2020, 1, 1)

    sql = text(f"""
        WITH {aum_cte}
        SELECT
            a.periodo,
            ROUND(SUM(a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS total_aum_clp,
            COUNT(DISTINCT a.run_fondo)::int     AS funds_count,
            COUNT(DISTINCT f.administrador)::int AS agfs_count
        FROM aportantes_fi a
        JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
        LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
        WHERE a.rut = :rut AND a.periodo >= :from_date
        GROUP BY a.periodo
        ORDER BY a.periodo ASC
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, {"rut": rut, "from_date": effective_from}).mappings().all()
    return [EvolutionPoint(**dict(r)) for r in rows]


# ── 4. Wallet share by AGF ─────────────────────────────────────────────────────

@router.get("/entity/{rut}/wallet-share", response_model=list[AGFShare])
def investor_wallet_share(
    rut: str,
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
) -> list[AGFShare]:
    """How this investor distributes their FI capital across administradoras.

    The competitive intelligence view: which AGFs are winning this client's
    wallet and how much? Sorted by AUM descending.
    """
    aum_cte = _FUND_AUM_CTE.format(extra_filter="")
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi WHERE rut = :rut)"
    params: dict = {"rut": rut}
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte},
        by_agf AS (
            SELECT
                f.administrador,
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum_clp,
                COUNT(DISTINCT a.run_fondo)::int            AS funds_count
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut = :rut AND a.periodo = {period_expr}
            GROUP BY f.administrador
        ),
        total AS (SELECT SUM(aum_clp) AS t FROM by_agf)
        SELECT
            b.administrador,
            ROUND(b.aum_clp::numeric, 0)                                     AS aum_clp,
            CASE WHEN t.t > 0 THEN ROUND((b.aum_clp / t.t * 100)::numeric, 2) ELSE 0 END AS pct_of_wallet,
            b.funds_count
        FROM by_agf b
        CROSS JOIN total t
        ORDER BY b.aum_clp DESC NULLS LAST
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [AGFShare(**dict(r)) for r in rows]


# ── 5. Fund concentration risk ─────────────────────────────────────────────────

@router.get("/fund/{run}/concentration", response_model=list[ConcentrationPeriod])
def fund_concentration(
    run: str,
    _: CacheHook,
    period: date | None = Query(None, description="Specific quarter. Defaults to all available."),
) -> list[ConcentrationPeriod]:
    """Ownership concentration metrics for a fund — across all quarters or a specific one.

    Returns top-1/3/5/10 shareholder concentration (% and CLP) plus Herfindahl
    index. A fund with HHI > 5000 or top-1 > 50% is dangerously captive — one
    redemption can force a fire sale of the entire portfolio.
    """
    aum_cte = _FUND_AUM_CTE.format(extra_filter="AND c.run_fondo = :run")
    period_filter = "AND a.periodo = :period" if period else ""
    params: dict = {"run": run}
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte},
        positions AS (
            SELECT
                a.periodo,
                a.rank,
                a.pct_propiedad,
                ROUND((a.pct_propiedad / 100.0 * fa.aum_clp)::numeric, 0) AS pos_aum_clp,
                ROUND(fa.aum_clp::numeric, 0)                              AS fund_aum_clp
            FROM aportantes_fi a
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.run_fondo = :run {period_filter}
        )
        SELECT
            p.periodo,
            MAX(p.fund_aum_clp)                                            AS fund_aum_clp,
            COUNT(*)::int                                                   AS shareholders_reported,
            ROUND(SUM(CASE WHEN p.rank = 1  THEN p.pct_propiedad END)::numeric, 4)  AS top1_pct,
            ROUND(SUM(CASE WHEN p.rank <= 3 THEN p.pct_propiedad END)::numeric, 4)  AS top3_pct,
            ROUND(SUM(CASE WHEN p.rank <= 5 THEN p.pct_propiedad END)::numeric, 4)  AS top5_pct,
            ROUND(SUM(CASE WHEN p.rank <= 10 THEN p.pct_propiedad END)::numeric, 4) AS top10_pct,
            ROUND(SUM(CASE WHEN p.rank = 1  THEN p.pos_aum_clp END)::numeric, 0)    AS top1_aum_clp,
            ROUND(SUM(CASE WHEN p.rank <= 3 THEN p.pos_aum_clp END)::numeric, 0)    AS top3_aum_clp,
            ROUND(SUM(CASE WHEN p.rank <= 5 THEN p.pos_aum_clp END)::numeric, 0)    AS top5_aum_clp,
            ROUND(SUM(p.pct_propiedad ^ 2)::numeric, 2)                             AS herfindahl_index
        FROM positions p
        GROUP BY p.periodo
        ORDER BY p.periodo DESC
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    result = []
    for r in rows:
        hhi = float(r["herfindahl_index"] or 0)
        top1 = float(r["top1_pct"] or 0)
        if hhi > 5000 or top1 > 50:
            label = "captive"
        elif hhi > 2500 or top1 > 30:
            label = "concentrated"
        elif hhi > 1500:
            label = "moderate"
        else:
            label = "diversified"
        result.append(ConcentrationPeriod(**dict(r), concentration_label=label))
    return result


# ── 6. Retention radar ─────────────────────────────────────────────────────────

@router.get("/admin/retention", response_model=list[RetentionEntry])
def admin_retention(
    pagination: Pagination,
    _: CacheHook,
    admin: str = Query(..., description="Administrador name (partial match)"),
    current_period: date | None = Query(None, description="Current quarter. Defaults to latest."),
    prev_period: date | None = Query(None, description="Comparison quarter. Defaults to one quarter before current."),
) -> list[RetentionEntry]:
    """Quarter-over-quarter AUM change for every shareholder in an admin's funds.

    Each investor is labeled: new (not in previous quarter), growing (+>5%),
    stable (±5%), shrinking (>-5%), or exiting (not in current quarter).
    Sort order: exiting first (highest risk), then shrinking, then by AUM desc.

    This is the retention dashboard — who is leaving, who is growing, who is new.
    """
    limit, offset = pagination
    aum_cte = _FUND_AUM_CTE.format(
        extra_filter="AND c.run_fondo IN (SELECT run_fondo FROM fondos_inversion WHERE administrador ILIKE :admin)"
    )

    # Resolve periods
    current_expr = ":current_period" if current_period else \
        "(SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin)"
    params: dict = {"admin": f"%{admin}%", "limit": limit, "offset": offset}
    if current_period:
        params["current_period"] = current_period
    if prev_period:
        params["prev_period"] = prev_period

    prev_expr = ":prev_period" if prev_period else f"""(
        SELECT MAX(x.periodo) FROM aportantes_fi x
        JOIN fondos_inversion g ON g.run_fondo = x.run_fondo
        WHERE g.administrador ILIKE :admin AND x.periodo < ({current_expr})
    )"""

    sql = text(f"""
        WITH {aum_cte},
        curr AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                a.tipo_persona,
                COUNT(DISTINCT a.run_fondo)::int        AS funds_count,
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum_clp,
                MAX(a.periodo)                          AS periodo
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE f.administrador ILIKE :admin
              AND a.rut IS NOT NULL
              AND a.periodo = {current_expr}
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        ),
        prev AS (
            SELECT
                a.rut,
                COUNT(DISTINCT a.run_fondo)::int        AS funds_count,
                SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum_clp,
                MAX(a.periodo)                          AS periodo
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE f.administrador ILIKE :admin
              AND a.rut IS NOT NULL
              AND a.periodo = {prev_expr}
            GROUP BY a.rut
        ),
        combined AS (
            SELECT
                COALESCE(c.rut,          p.rut)         AS rut,
                c.nombre,
                c.tipo_persona,
                c.aum_clp                               AS current_aum_clp,
                p.aum_clp                               AS prev_aum_clp,
                c.aum_clp - p.aum_clp                  AS delta_clp,
                CASE WHEN p.aum_clp > 0
                     THEN (c.aum_clp - p.aum_clp) / p.aum_clp * 100
                     ELSE NULL END                      AS delta_pct,
                COALESCE(c.funds_count, 0)              AS funds_current,
                COALESCE(p.funds_count, 0)              AS funds_prev,
                c.periodo                               AS current_period,
                p.periodo                               AS prev_period,
                CASE
                    WHEN c.rut IS NULL THEN 'exiting'
                    WHEN p.rut IS NULL THEN 'new'
                    WHEN c.aum_clp > p.aum_clp * 1.05  THEN 'growing'
                    WHEN c.aum_clp < p.aum_clp * 0.95  THEN 'shrinking'
                    ELSE 'stable'
                END AS status
            FROM curr c
            FULL OUTER JOIN prev p ON p.rut = c.rut
        )
        SELECT *
        FROM combined
        ORDER BY
            CASE status WHEN 'exiting' THEN 0 WHEN 'shrinking' THEN 1
                        WHEN 'stable' THEN 2 WHEN 'growing' THEN 3
                        WHEN 'new' THEN 4 END,
            COALESCE(current_aum_clp, prev_aum_clp) DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    return [
        RetentionEntry(
            rut=r["rut"],
            nombre=r["nombre"],
            tipo_persona=r["tipo_persona"],
            current_aum_clp=float(r["current_aum_clp"]) if r["current_aum_clp"] is not None else None,
            prev_aum_clp=float(r["prev_aum_clp"]) if r["prev_aum_clp"] is not None else None,
            delta_clp=float(r["delta_clp"]) if r["delta_clp"] is not None else None,
            delta_pct=float(r["delta_pct"]) if r["delta_pct"] is not None else None,
            status=r["status"],
            funds_current=r["funds_current"],
            funds_prev=r["funds_prev"],
            current_period=r["current_period"],
            prev_period=r["prev_period"],
        )
        for r in rows
    ]


# ── 7. Market wallet matrix ───────────────────────────────────────────────────

@router.get("/matrix", response_model=MatrixResponse)
def shareholder_matrix(
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
    min_aum_clp: float | None = Query(None, ge=0),
    admin: str | None = Query(None, description="Only investors using this administrador"),
    tipo_persona: str | None = Query(None, description="J=juridica, N=natural"),
    limit: int = Query(100, ge=1, le=500),
) -> MatrixResponse:
    """AGF x investor wallet-share matrix for the FI shareholder market."""
    aum_cte = _FUND_AUM_CTE.format(
        extra_filter="AND c.periodo = :period" if period else "AND c.periodo = (SELECT MAX(periodo) FROM aportantes_fi)"
    )
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi)"
    having_clause = "HAVING SUM(aum_clp) >= :min_aum_clp" if min_aum_clp is not None else ""
    admin_filter = "AND EXISTS (SELECT 1 FROM by_agf bx WHERE bx.rut = t.rut AND bx.administrador ILIKE :admin)" if admin else ""
    tipo_filter = "AND a.tipo_persona = :tipo_persona" if tipo_persona else ""

    params: dict = {"limit": limit}
    if period:
        params["period"] = period
    if min_aum_clp is not None:
        params["min_aum_clp"] = min_aum_clp
    if admin:
        params["admin"] = f"%{admin}%"
    if tipo_persona:
        params["tipo_persona"] = tipo_persona

    sql = text(f"""
        WITH {aum_cte},
        positions AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                f.administrador,
                a.run_fondo,
                a.pct_propiedad / 100.0 * fa.aum_clp AS aum_clp
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL
              AND a.periodo = {period_expr}
              {tipo_filter}
        ),
        by_agf AS (
            SELECT rut, nombre, administrador,
                   SUM(aum_clp) AS aum_clp,
                   COUNT(DISTINCT run_fondo)::int AS funds_count
            FROM positions
            GROUP BY rut, nombre, administrador
        ),
        totals AS (
            SELECT rut, MAX(nombre) AS nombre,
                   SUM(aum_clp) AS total_aum_clp,
                   COUNT(*)::int AS agfs_count,
                   SUM(CASE WHEN administrador ILIKE '%BTG%' THEN aum_clp ELSE 0 END) AS btg_aum_clp
            FROM by_agf
            GROUP BY rut
            {having_clause}
        ),
        limited AS (
            SELECT t.*
            FROM totals t
            WHERE 1=1 {admin_filter}
            ORDER BY t.total_aum_clp DESC NULLS LAST
            LIMIT :limit
        )
        SELECT
            (SELECT {period_expr}) AS periodo,
            l.rut, l.nombre, l.total_aum_clp, l.agfs_count, l.btg_aum_clp,
            b.administrador, b.aum_clp, b.funds_count
        FROM limited l
        JOIN by_agf b ON b.rut = l.rut
        ORDER BY l.total_aum_clp DESC NULLS LAST, b.aum_clp DESC NULLS LAST
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    if not rows:
        effective_period = period
        if effective_period is None:
            with SessionLocal() as session:
                effective_period = session.execute(text("SELECT MAX(periodo) FROM aportantes_fi")).scalar_one()
        return MatrixResponse(periodo=effective_period, investors=[])

    investors: dict[str, MatrixInvestor] = {}
    for r in rows:
        rut = r["rut"]
        total = float(r["total_aum_clp"] or 0)
        if rut not in investors:
            btg_aum = float(r["btg_aum_clp"] or 0)
            investors[rut] = MatrixInvestor(
                rut=rut,
                nombre=r["nombre"],
                total_aum_clp=total,
                agfs_count=r["agfs_count"],
                btg_aum_clp=btg_aum,
                btg_wallet_pct=round(btg_aum / total * 100, 2) if total else 0,
                rows=[],
            )
        aum = float(r["aum_clp"] or 0)
        investors[rut].rows.append(
            MatrixAGFRow(
                administrador=r["administrador"],
                aum_clp=aum,
                pct_of_wallet=round(aum / total * 100, 2) if total else 0,
                funds_count=r["funds_count"],
            )
        )

    return MatrixResponse(periodo=rows[0]["periodo"], investors=list(investors.values()))


# ── 8. BTG opportunity ranking ────────────────────────────────────────────────

@router.get("/opportunities", response_model=list[OpportunityEntry])
def shareholder_opportunities(
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
    tipo_persona: str | None = Query("J", description="J=juridica, N=natural"),
    min_aum_clp: float = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[OpportunityEntry]:
    """Investors with large wallets where BTG has low or zero share."""
    aum_cte = _FUND_AUM_CTE.format(
        extra_filter="AND c.periodo = :period" if period else "AND c.periodo = (SELECT MAX(periodo) FROM aportantes_fi)"
    )
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi)"
    tipo_filter = "AND a.tipo_persona = :tipo_persona" if tipo_persona else ""
    params: dict = {"min_aum_clp": min_aum_clp, "limit": limit}
    if period:
        params["period"] = period
    if tipo_persona:
        params["tipo_persona"] = tipo_persona

    sql = text(f"""
        WITH {aum_cte},
        positions AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                f.administrador,
                a.run_fondo,
                a.pct_propiedad / 100.0 * fa.aum_clp AS aum_clp
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa   ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL
              AND a.periodo = {period_expr}
              {tipo_filter}
        ),
        by_agf AS (
            SELECT rut, MAX(nombre) AS nombre, administrador,
                   SUM(aum_clp) AS aum_clp,
                   COUNT(DISTINCT run_fondo)::int AS funds_count
            FROM positions
            GROUP BY rut, administrador
        ),
        ranked_agf AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY rut ORDER BY aum_clp DESC NULLS LAST) AS rn
            FROM by_agf
        ),
        totals AS (
            SELECT
                rut,
                MAX(nombre) AS nombre,
                SUM(aum_clp) AS total_aum_clp,
                SUM(CASE WHEN administrador ILIKE '%BTG%' THEN aum_clp ELSE 0 END) AS btg_aum_clp,
                COUNT(*)::int AS agfs_count
            FROM by_agf
            GROUP BY rut
        )
        SELECT
            t.rut, t.nombre, t.total_aum_clp,
            COALESCE(t.btg_aum_clp, 0) AS btg_aum_clp,
            t.agfs_count,
            r.administrador AS main_agf,
            CASE WHEN t.total_aum_clp > 0 THEN r.aum_clp / t.total_aum_clp * 100 ELSE 0 END AS main_agf_wallet_pct
        FROM totals t
        JOIN ranked_agf r ON r.rut = t.rut AND r.rn = 1
        WHERE t.total_aum_clp >= :min_aum_clp
          AND COALESCE(t.btg_aum_clp, 0) / NULLIF(t.total_aum_clp, 0) <= 0.05
        ORDER BY (t.total_aum_clp - COALESCE(t.btg_aum_clp, 0)) DESC NULLS LAST
        LIMIT :limit
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    result = []
    for r in rows:
        total = float(r["total_aum_clp"] or 0)
        btg = float(r["btg_aum_clp"] or 0)
        btg_pct = btg / total * 100 if total else 0
        main_pct = float(r["main_agf_wallet_pct"] or 0)
        opportunity = max(total - btg, 0)
        score = opportunity * (1.25 if btg_pct == 0 else 1.0) * (1.15 if main_pct >= 50 else 1.0)
        if btg_pct == 0:
            reason = "BTG has no reported wallet share"
        elif main_pct >= 50:
            reason = "Low BTG share and wallet concentrated with main competitor"
        else:
            reason = "BTG wallet share is below 5%"
        result.append(
            OpportunityEntry(
                rut=r["rut"],
                nombre=r["nombre"],
                total_aum_clp=total,
                btg_aum_clp=btg,
                btg_wallet_pct=round(btg_pct, 2),
                agfs_count=r["agfs_count"],
                main_agf=r["main_agf"],
                main_agf_wallet_pct=round(main_pct, 2),
                opportunity_aum_clp=opportunity,
                score=round(score, 2),
                reason=reason,
            )
        )
    return result


# ── 9. Investor flow radar ────────────────────────────────────────────────────

@router.get("/flows", response_model=list[FlowEntry])
def shareholder_flows(
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Optional administrador partial match"),
    period: date | None = Query(None, description="Current quarter. Defaults to latest."),
    status: str | None = Query(None, description="new | growing | stable | shrinking | exiting"),
    min_abs_delta_clp: float | None = Query(None, ge=0),
) -> list[FlowEntry]:
    """Market-wide quarterly movement by aportante."""
    limit, offset = pagination
    current_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi)"
    prev_expr = f"(SELECT MAX(periodo) FROM aportantes_fi WHERE periodo < ({current_expr}))"
    aum_cte = _FUND_AUM_CTE.format(extra_filter=f"AND c.periodo IN ({current_expr}, {prev_expr})")
    admin_filter = "AND f.administrador ILIKE :admin" if admin else ""
    status_filter = "WHERE status = :status" if status else "WHERE 1=1"
    delta_filter = "AND ABS(delta_clp) >= :min_abs_delta_clp" if min_abs_delta_clp is not None else ""

    params: dict = {"limit": limit, "offset": offset}
    if period:
        params["period"] = period
    if admin:
        params["admin"] = f"%{admin}%"
    if status:
        params["status"] = status
    if min_abs_delta_clp is not None:
        params["min_abs_delta_clp"] = min_abs_delta_clp

    sql = text(f"""
        WITH {aum_cte},
        curr AS (
            SELECT a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                   SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum_clp,
                   COUNT(DISTINCT f.administrador)::int AS agfs_count,
                   MAX(a.periodo) AS periodo
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL AND a.periodo = {current_expr} {admin_filter}
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre)
        ),
        prev AS (
            SELECT a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                   SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum_clp,
                   COUNT(DISTINCT f.administrador)::int AS agfs_count,
                   MAX(a.periodo) AS periodo
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL AND a.periodo = {prev_expr} {admin_filter}
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre)
        ),
        combined AS (
            SELECT
                COALESCE(c.rut, p.rut) AS rut,
                COALESCE(c.nombre, p.nombre) AS nombre,
                c.aum_clp AS current_aum_clp,
                p.aum_clp AS prev_aum_clp,
                COALESCE(c.aum_clp, 0) - COALESCE(p.aum_clp, 0) AS delta_clp,
                CASE WHEN p.aum_clp > 0 THEN (COALESCE(c.aum_clp, 0) - p.aum_clp) / p.aum_clp * 100 ELSE NULL END AS delta_pct,
                COALESCE(c.agfs_count, 0) AS current_agfs_count,
                COALESCE(p.agfs_count, 0) AS prev_agfs_count,
                c.periodo AS current_period,
                p.periodo AS prev_period,
                CASE
                    WHEN c.rut IS NULL THEN 'exiting'
                    WHEN p.rut IS NULL THEN 'new'
                    WHEN c.aum_clp > p.aum_clp * 1.05 THEN 'growing'
                    WHEN c.aum_clp < p.aum_clp * 0.95 THEN 'shrinking'
                    ELSE 'stable'
                END AS status
            FROM curr c
            FULL OUTER JOIN prev p ON p.rut = c.rut
        )
        SELECT *
        FROM combined
        {status_filter}
          {delta_filter}
        ORDER BY ABS(delta_clp) DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    return [
        FlowEntry(
            rut=r["rut"],
            nombre=r["nombre"],
            current_aum_clp=float(r["current_aum_clp"]) if r["current_aum_clp"] is not None else None,
            prev_aum_clp=float(r["prev_aum_clp"]) if r["prev_aum_clp"] is not None else None,
            delta_clp=float(r["delta_clp"]) if r["delta_clp"] is not None else None,
            delta_pct=float(r["delta_pct"]) if r["delta_pct"] is not None else None,
            status=r["status"],
            current_agfs_count=r["current_agfs_count"],
            prev_agfs_count=r["prev_agfs_count"],
            current_period=r["current_period"],
            prev_period=r["prev_period"],
        )
        for r in rows
    ]


# ── 10. Fund concentration ranking ────────────────────────────────────────────

@router.get("/fund-concentration/ranking", response_model=list[FundConcentrationRankingEntry])
def fund_concentration_ranking(
    pagination: Pagination,
    _: CacheHook,
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
    admin: str | None = Query(None, description="Optional administrador partial match"),
) -> list[FundConcentrationRankingEntry]:
    """Rank funds by shareholder concentration risk."""
    limit, offset = pagination
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi)"
    aum_cte = _FUND_AUM_CTE.format(extra_filter="AND c.periodo = :period" if period else "AND c.periodo = (SELECT MAX(periodo) FROM aportantes_fi)")
    admin_filter = "AND f.administrador ILIKE :admin" if admin else ""
    params: dict = {"limit": limit, "offset": offset}
    if period:
        params["period"] = period
    if admin:
        params["admin"] = f"%{admin}%"

    sql = text(f"""
        WITH {aum_cte},
        positions AS (
            SELECT
                a.run_fondo, f.razon_social, f.administrador, a.periodo,
                a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                a.rank, a.pct_propiedad,
                fa.aum_clp AS fund_aum_clp,
                a.pct_propiedad / 100.0 * fa.aum_clp AS holder_aum_clp
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.periodo = {period_expr} {admin_filter}
        ),
        agg AS (
            SELECT
                run_fondo, MAX(razon_social) AS razon_social, MAX(administrador) AS administrador,
                periodo, MAX(fund_aum_clp) AS fund_aum_clp,
                COUNT(*)::int AS shareholders_reported,
                SUM(CASE WHEN rank = 1 THEN pct_propiedad END) AS top1_pct,
                SUM(CASE WHEN rank <= 3 THEN pct_propiedad END) AS top3_pct,
                SUM(CASE WHEN rank <= 5 THEN pct_propiedad END) AS top5_pct,
                SUM(CASE WHEN rank <= 10 THEN pct_propiedad END) AS top10_pct,
                SUM(pct_propiedad ^ 2) AS herfindahl_index
            FROM positions
            GROUP BY run_fondo, periodo
        ),
        largest AS (
            SELECT DISTINCT ON (run_fondo, periodo)
                run_fondo, periodo, rut, nombre, pct_propiedad, holder_aum_clp
            FROM positions
            ORDER BY run_fondo, periodo, pct_propiedad DESC NULLS LAST
        )
        SELECT
            a.*, l.rut AS largest_rut, l.nombre AS largest_nombre,
            l.pct_propiedad AS largest_pct_propiedad,
            l.holder_aum_clp AS largest_aum_clp
        FROM agg a
        JOIN largest l ON l.run_fondo = a.run_fondo AND l.periodo = a.periodo
        ORDER BY a.herfindahl_index DESC NULLS LAST, a.top1_pct DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    result = []
    for r in rows:
        hhi = float(r["herfindahl_index"] or 0)
        top1 = float(r["top1_pct"] or 0)
        if hhi > 5000 or top1 > 50:
            label = "captive"
        elif hhi > 2500 or top1 > 30:
            label = "concentrated"
        elif hhi > 1500:
            label = "moderate"
        else:
            label = "diversified"
        result.append(
            FundConcentrationRankingEntry(
                run_fondo=r["run_fondo"],
                razon_social=r["razon_social"],
                administrador=r["administrador"],
                periodo=r["periodo"],
                fund_aum_clp=float(r["fund_aum_clp"]) if r["fund_aum_clp"] is not None else None,
                shareholders_reported=r["shareholders_reported"],
                top1_pct=float(r["top1_pct"]) if r["top1_pct"] is not None else None,
                top3_pct=float(r["top3_pct"]) if r["top3_pct"] is not None else None,
                top5_pct=float(r["top5_pct"]) if r["top5_pct"] is not None else None,
                top10_pct=float(r["top10_pct"]) if r["top10_pct"] is not None else None,
                herfindahl_index=hhi,
                concentration_label=label,
                largest_holder=LargestHolder(
                    rut=r["largest_rut"],
                    nombre=r["largest_nombre"],
                    pct_propiedad=float(r["largest_pct_propiedad"]) if r["largest_pct_propiedad"] is not None else None,
                    aum_clp=float(r["largest_aum_clp"]) if r["largest_aum_clp"] is not None else None,
                ),
            )
        )
    return result


# ── 11. Advanced merger simulator ─────────────────────────────────────────────

@router.get("/merge-simulation", response_model=MergeSimulationResponse)
def merge_simulation(
    _: CacheHook,
    admin_a: str = Query(..., description="First administrador (partial match)"),
    admin_b: str = Query(..., description="Second administrador (partial match)"),
    period: date | None = Query(None, description="Quarter-end date. Defaults to latest."),
) -> MergeSimulationResponse:
    """Advanced AGF merger simulation with overlap, concentration, and cross-sell metrics."""
    period_expr = ":period" if period else "(SELECT MAX(periodo) FROM aportantes_fi)"
    aum_cte = _FUND_AUM_CTE.format(extra_filter="AND c.periodo = :period" if period else "AND c.periodo = (SELECT MAX(periodo) FROM aportantes_fi)")
    params: dict = {"admin_a": f"%{admin_a}%", "admin_b": f"%{admin_b}%"}
    if period:
        params["period"] = period

    sql = text(f"""
        WITH {aum_cte},
        market AS (
            SELECT a.rut, SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS market_total_aum_clp
            FROM aportantes_fi a
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL AND a.periodo = {period_expr}
            GROUP BY a.rut
        ),
        holders_a AS (
            SELECT a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                   SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL AND a.periodo = {period_expr} AND f.administrador ILIKE :admin_a
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre)
        ),
        holders_b AS (
            SELECT a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre,
                   SUM(a.pct_propiedad / 100.0 * fa.aum_clp) AS aum
            FROM aportantes_fi a
            JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
            LEFT JOIN fund_aum fa ON fa.run_fondo = a.run_fondo AND fa.periodo = a.periodo
            WHERE a.rut IS NOT NULL AND a.periodo = {period_expr} AND f.administrador ILIKE :admin_b
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre)
        ),
        totals AS (
            SELECT
                (SELECT COALESCE(SUM(aum), 0) FROM holders_a) AS total_a,
                (SELECT COALESCE(SUM(aum), 0) FROM holders_b) AS total_b
        ),
        combined AS (
            SELECT
                COALESCE(a.rut, b.rut) AS rut,
                COALESCE(a.nombre, b.nombre) AS nombre,
                a.aum AS aum_a,
                b.aum AS aum_b,
                COALESCE(a.aum, 0) + COALESCE(b.aum, 0) AS merged_aum,
                CASE
                    WHEN a.rut IS NOT NULL AND b.rut IS NOT NULL THEN 'shared'
                    WHEN a.rut IS NOT NULL THEN 'only_a'
                    ELSE 'only_b'
                END AS segment
            FROM holders_a a
            FULL OUTER JOIN holders_b b ON b.rut = a.rut
        )
        SELECT
            c.*, m.market_total_aum_clp,
            CASE WHEN t.total_a > 0 THEN c.aum_a / t.total_a * 100 ELSE NULL END AS wallet_share_a,
            CASE WHEN t.total_b > 0 THEN c.aum_b / t.total_b * 100 ELSE NULL END AS wallet_share_b,
            CASE WHEN t.total_a + t.total_b > 0 THEN c.merged_aum / (t.total_a + t.total_b) * 100 ELSE 0 END AS concentration_after_pct,
            t.total_a, t.total_b
        FROM combined c
        LEFT JOIN market m ON m.rut = c.rut
        CROSS JOIN totals t
        ORDER BY c.merged_aum DESC NULLS LAST
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()

    if not rows:
        raise HTTPException(404, "No shareholder data found for the given administrators")

    holders: list[MergeSimulationShareholder] = []
    for r in rows:
        holders.append(
            MergeSimulationShareholder(
                rut=r["rut"],
                nombre=r["nombre"],
                aum_a=float(r["aum_a"]) if r["aum_a"] is not None else None,
                aum_b=float(r["aum_b"]) if r["aum_b"] is not None else None,
                merged_aum=float(r["merged_aum"] or 0),
                wallet_share_a=float(r["wallet_share_a"]) if r["wallet_share_a"] is not None else None,
                wallet_share_b=float(r["wallet_share_b"]) if r["wallet_share_b"] is not None else None,
                market_total_aum_clp=float(r["market_total_aum_clp"] or 0),
                concentration_after_pct=float(r["concentration_after_pct"] or 0),
            )
        )

    total_a = float(rows[0]["total_a"] or 0)
    total_b = float(rows[0]["total_b"] or 0)
    merged_total = total_a + total_b
    shared = [h for h in holders if h.aum_a is not None and h.aum_b is not None]
    only_a = [h for h in holders if h.aum_a is not None and h.aum_b is None]
    only_b = [h for h in holders if h.aum_a is None and h.aum_b is not None]

    def hhi(values: list[float], total: float) -> float:
        if total <= 0:
            return 0
        return round(sum((v / total * 100) ** 2 for v in values), 2)

    hhi_a = hhi([h.aum_a or 0 for h in holders if h.aum_a is not None], total_a)
    hhi_b = hhi([h.aum_b or 0 for h in holders if h.aum_b is not None], total_b)
    hhi_after = hhi([h.merged_aum for h in holders], merged_total)
    top10_after_pct = round(sum(h.merged_aum for h in holders[:10]) / merged_total * 100, 2) if merged_total else 0
    overlap_aum = sum(h.merged_aum for h in shared)
    cross_sell_targets = sorted(
        [h for h in only_a + only_b if h.market_total_aum_clp > h.merged_aum],
        key=lambda h: h.market_total_aum_clp - h.merged_aum,
        reverse=True,
    )[:25]
    high_risk_overlap = [h for h in shared if h.concentration_after_pct >= 5 or (h.wallet_share_a or 0) >= 20 or (h.wallet_share_b or 0) >= 20]
    cross_sell_opportunity = sum(max(h.market_total_aum_clp - h.merged_aum, 0) for h in cross_sell_targets)
    retention_risk = sum(h.merged_aum for h in high_risk_overlap)

    return MergeSimulationResponse(
        summary=MergeSimulationSummary(
            admin_a=admin_a,
            admin_b=admin_b,
            merged_aum_clp=merged_total,
            overlap_aum_clp=overlap_aum,
            overlap_pct_of_merged=round(overlap_aum / merged_total * 100, 2) if merged_total else 0,
            shared_shareholders=len(shared),
            exclusive_shareholders=len(only_a) + len(only_b),
            concentration_hhi_before_a=hhi_a,
            concentration_hhi_before_b=hhi_b,
            concentration_hhi_after=hhi_after,
            top10_after_pct=top10_after_pct,
            cross_sell_opportunity_clp=cross_sell_opportunity,
            retention_risk_clp=retention_risk,
        ),
        segments=MergeSimulationSegments(
            shared=shared,
            only_a=only_a,
            only_b=only_b,
            high_risk_overlap=high_risk_overlap,
            cross_sell_targets=cross_sell_targets,
        ),
    )
