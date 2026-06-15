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
