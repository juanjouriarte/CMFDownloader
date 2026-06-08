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
