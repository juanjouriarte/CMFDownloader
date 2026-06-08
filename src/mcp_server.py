from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text

from src.db.engine import SessionLocal

mcp = FastMCP(
    "BTG CMF Assistant",
    instructions="""
You are an expert analyst of the Chilean fund industry with access to the full CMF database.
You have real-time data on ~1,340 mutual funds (FM) and ~1,641 investment funds (FI) from all
36 administradoras (AGFs) in Chile. Data covers NAV, AUM, flows, rentability, quarterly
portfolios, and shareholders from 2020 to today.

When answering questions:
- Always provide context: market share, rankings, comparisons
- For AUM figures, express in CLP billions (divide by 1,000,000,000) for readability
- Net new money = inflows (aportes) - outflows (rescates) — positive means the fund/AGF attracted capital
- Rentability figures are percentages (already multiplied by 100 for FM, direct for FI)
- When comparing funds, always note the fund type (FM vs FI) and currency (CLP vs USD)
- Use multiple tools to build a complete picture before answering
""",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_PERIOD_SQL = {
    "this_month":  "fecha >= DATE_TRUNC('month', CURRENT_DATE)",
    "last_month":  "fecha >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') AND fecha < DATE_TRUNC('month', CURRENT_DATE)",
    "last_3m":     "fecha >= CURRENT_DATE - INTERVAL '3 months'",
    "last_6m":     "fecha >= CURRENT_DATE - INTERVAL '6 months'",
    "ytd":         "fecha >= DATE_TRUNC('year', CURRENT_DATE)",
    "last_12m":    "fecha >= CURRENT_DATE - INTERVAL '12 months'",
}

_SORT_COLS = {"r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"}


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with SessionLocal() as s:
        result = s.execute(text(sql), params or {})
        keys = result.keys()
        return [dict(zip(keys, row)) for row in result]


def _scalar(sql: str, params: dict | None = None):
    with SessionLocal() as s:
        return s.execute(text(sql), params or {}).scalar_one_or_none()


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_funds(
    query: str,
    fund_type: Literal["fm", "fi", "all"] = "all",
    vigente: bool = True,
    limit: int = 20,
) -> list[dict]:
    """
    Search for mutual funds (FM) or investment funds (FI) by name or administrator.
    Returns fund identity + latest returns. Use this first to find run_fondo values
    before calling other tools.
    """
    results = []

    if fund_type in ("fm", "all"):
        vigente_filter = "AND fecha_termino_operaciones IS NULL" if vigente else ""
        rows = _rows(f"""
            SELECT
                'fm'                                    AS tipo,
                fm.run_fondo,
                fm.nombre_fondo                         AS nombre,
                fm.razon_social_administradora          AS administrador,
                fm.tipo_fondo,
                fm.moneda,
                fecha_termino_operaciones IS NULL       AS vigente,
                r.r_1m, r.r_1y, r.r_ytd,
                r.valor_actual                          AS valor_cuota_actual,
                r.fecha_calculo
            FROM fondo_mutuo fm
            LEFT JOIN mv_rentabilidad_fm r ON r.run_fondo = fm.run_fondo
            WHERE (fm.nombre_fondo ILIKE :q OR fm.razon_social_administradora ILIKE :q)
              {vigente_filter}
            ORDER BY fm.nombre_fondo
            LIMIT :limit
        """, {"q": f"%{query}%", "limit": limit})
        results.extend(rows)

    if fund_type in ("fi", "all"):
        vigente_filter = "AND fi.vigente = true" if vigente else ""
        rows = _rows(f"""
            SELECT
                'fi'                                    AS tipo,
                fi.run_fondo,
                fi.razon_social                         AS nombre,
                fi.administrador,
                NULL                                    AS tipo_fondo,
                NULL                                    AS moneda,
                fi.vigente,
                r.r_1m, r.r_1y, r.r_ytd,
                r.valor_actual                          AS valor_cuota_actual,
                r.fecha_calculo
            FROM fondos_inversion fi
            LEFT JOIN mv_rentabilidad_fi r ON r.run_fondo = fi.run_fondo
            WHERE (fi.razon_social ILIKE :q OR fi.administrador ILIKE :q)
              {vigente_filter}
            ORDER BY fi.razon_social
            LIMIT :limit
        """, {"q": f"%{query}%", "limit": limit})
        results.extend(rows)

    return results


@mcp.tool()
def compare_funds(
    run_fondos: list[str],
    fund_type: Literal["fm", "fi"] = "fm",
) -> list[dict]:
    """
    Side-by-side rentability comparison of 2 or more funds.
    Pass a list of run_fondo values (use search_funds first to find them).
    Returns all return periods (1D, 1W, 1M, 1Y, 5Y, YTD) plus AUM and latest NAV.
    """
    placeholders = ", ".join(f":r{i}" for i in range(len(run_fondos)))
    params = {f"r{i}": r for i, r in enumerate(run_fondos)}

    if fund_type == "fm":
        return _rows(f"""
            SELECT
                r.run_fondo, r.serie, r.nombre_fondo, r.administrador,
                r.valor_actual, r.fecha_calculo,
                r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd,
                cd.patrimonio_neto AS aum_clp
            FROM mv_rentabilidad_fm r
            LEFT JOIN LATERAL (
                SELECT patrimonio_neto FROM cartola_diaria
                WHERE run_fondo = r.run_fondo AND serie = r.serie
                ORDER BY fecha DESC LIMIT 1
            ) cd ON true
            WHERE r.run_fondo IN ({placeholders})
            ORDER BY r.r_1y DESC NULLS LAST
        """, params)
    else:
        return _rows(f"""
            SELECT
                r.run_fondo, r.serie, f.razon_social AS nombre, r.administrador,
                r.valor_actual, r.fecha_calculo,
                r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd,
                vc.patrimonio_neto AS aum_clp
            FROM mv_rentabilidad_fi r
            LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
            LEFT JOIN LATERAL (
                SELECT patrimonio_neto FROM valores_cuota_fi
                WHERE run_fondo = r.run_fondo AND serie = r.serie
                ORDER BY fecha DESC LIMIT 1
            ) vc ON true
            WHERE r.run_fondo IN ({placeholders})
            ORDER BY r.r_1y DESC NULLS LAST
        """, params)


@mcp.tool()
def top_funds_by_return(
    fund_type: Literal["fm", "fi"] = "fm",
    sort_by: Literal["r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"] = "r_1y",
    admin: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Ranking of best performing funds by return period.
    fund_type: 'fm' for mutual funds, 'fi' for investment funds.
    sort_by: r_1d=1 day, r_1w=1 week, r_1m=1 month, r_1y=1 year, r_5y=5 years, r_ytd=year-to-date.
    """
    if sort_by not in _SORT_COLS:
        sort_by = "r_1y"

    admin_filter = "AND administrador ILIKE :admin" if admin else ""
    params: dict = {"limit": limit}
    if admin:
        params["admin"] = f"%{admin}%"

    if fund_type == "fm":
        return _rows(f"""
            SELECT run_fondo, serie, nombre_fondo, administrador,
                   valor_actual, fecha_calculo,
                   r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd
            FROM mv_rentabilidad_fm
            WHERE {sort_by} IS NOT NULL {admin_filter}
            ORDER BY {sort_by} DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            SELECT r.run_fondo, r.serie, f.razon_social AS nombre, r.administrador,
                   r.valor_actual, r.fecha_calculo,
                   r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
            FROM mv_rentabilidad_fi r
            LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
            WHERE r.{sort_by} IS NOT NULL {admin_filter}
            ORDER BY r.{sort_by} DESC NULLS LAST
            LIMIT :limit
        """, params)


@mcp.tool()
def net_new_money_ranking(
    period: Literal["this_month", "last_month", "last_3m", "last_6m", "ytd", "last_12m"] = "this_month",
    group_by: Literal["agf", "fund"] = "agf",
    limit: int = 20,
) -> list[dict]:
    """
    Rank AGFs or individual funds by net new money (aportes - rescates) for any period.
    Positive = attracted capital. Negative = net outflows.
    Only covers mutual funds (FM) — CMF does not publish FI flow data.
    """
    period_filter = _PERIOD_SQL[period]

    if group_by == "agf":
        return _rows(f"""
            SELECT
                fm.razon_social_administradora              AS administrador,
                ROUND(SUM(cd.monto_aportado)::numeric / 1e9, 2)     AS aportes_bn_clp,
                ROUND(SUM(cd.monto_rescatado)::numeric / 1e9, 2)    AS rescates_bn_clp,
                ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2) AS net_new_money_bn_clp,
                COUNT(DISTINCT cd.run_fondo)                AS num_fondos
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE {period_filter}
              AND cd.monto_aportado IS NOT NULL
            GROUP BY fm.razon_social_administradora
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, {"limit": limit})
    else:
        return _rows(f"""
            SELECT
                cd.run_fondo,
                fm.nombre_fondo,
                fm.razon_social_administradora              AS administrador,
                ROUND(SUM(cd.monto_aportado)::numeric / 1e9, 2)     AS aportes_bn_clp,
                ROUND(SUM(cd.monto_rescatado)::numeric / 1e9, 2)    AS rescates_bn_clp,
                ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2) AS net_new_money_bn_clp
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE {period_filter}
              AND cd.monto_aportado IS NOT NULL
            GROUP BY cd.run_fondo, fm.nombre_fondo, fm.razon_social_administradora
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, {"limit": limit})


@mcp.tool()
def get_fund_full_picture(run_fondo: str, fund_type: Literal["fm", "fi"] = "fm") -> dict:
    """
    Complete profile of a single fund: identity, all return periods, AUM trend (last 12 months),
    net new money (FM only), top shareholders (FI), and latest portfolio summary.
    """
    result: dict = {}

    if fund_type == "fm":
        # Identity + returns
        rows = _rows("""
            SELECT fm.run_fondo, fm.nombre_fondo, fm.nombre_corto,
                   fm.razon_social_administradora AS administrador,
                   fm.tipo_fondo, fm.moneda,
                   fm.fecha_inicio_operaciones,
                   fm.fecha_termino_operaciones IS NULL AS vigente,
                   r.serie, r.valor_actual, r.fecha_calculo,
                   r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
            FROM fondo_mutuo fm
            LEFT JOIN mv_rentabilidad_fm r ON r.run_fondo = fm.run_fondo
            WHERE fm.run_fondo = :run
        """, {"run": run_fondo})
        result["identity"] = rows

        # AUM + flows last 12 months (monthly)
        result["monthly_flows"] = _rows("""
            SELECT
                DATE_TRUNC('month', fecha)::date                    AS mes,
                ROUND(SUM(monto_aportado)::numeric / 1e9, 3)        AS aportes_bn_clp,
                ROUND(SUM(monto_rescatado)::numeric / 1e9, 3)       AS rescates_bn_clp,
                ROUND(SUM(monto_aportado - monto_rescatado)::numeric / 1e9, 3) AS net_new_money_bn_clp,
                ROUND(AVG(patrimonio_neto)::numeric / 1e9, 3)       AS aum_promedio_bn_clp
            FROM cartola_diaria
            WHERE run_fondo = :run
              AND fecha >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY DATE_TRUNC('month', fecha)
            ORDER BY mes DESC
        """, {"run": run_fondo})

        # Portfolio summary (latest quarter)
        result["portfolio_summary"] = _rows("""
            SELECT tipo_instrumento,
                   COUNT(*) AS posiciones,
                   ROUND(AVG(CAST(porcentaje_activos_fondo AS numeric)), 2) AS pct_promedio
            FROM cartera_naci
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run)
            GROUP BY tipo_instrumento
            ORDER BY pct_promedio DESC NULLS LAST
        """, {"run": run_fondo})

    else:
        # FI identity + returns
        rows = _rows("""
            SELECT fi.run_fondo, fi.razon_social AS nombre, fi.administrador,
                   fi.rescatable, fi.vigente,
                   r.serie, r.valor_actual, r.fecha_calculo,
                   r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
            FROM fondos_inversion fi
            LEFT JOIN mv_rentabilidad_fi r ON r.run_fondo = fi.run_fondo
            WHERE fi.run_fondo = :run
        """, {"run": run_fondo})
        result["identity"] = rows

        # AUM last 12 months (monthly)
        result["aum_trend"] = _rows("""
            SELECT
                DATE_TRUNC('month', fecha)::date                AS mes,
                ROUND(AVG(patrimonio_neto)::numeric / 1e9, 3)  AS aum_promedio_bn_clp
            FROM valores_cuota_fi
            WHERE run_fondo = :run
              AND fecha >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY DATE_TRUNC('month', fecha)
            ORDER BY mes DESC
        """, {"run": run_fondo})

        # Top shareholders latest quarter
        result["top_shareholders"] = _rows("""
            SELECT rank, nombre_canonical AS nombre, rut, tipo_persona, pct_propiedad
            FROM aportantes_fi
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM aportantes_fi WHERE run_fondo = :run)
            ORDER BY rank
        """, {"run": run_fondo})

    return result


@mcp.tool()
def get_administrator_full_picture(admin: str) -> dict:
    """
    Complete administradora profile: fund counts, total AUM, market share,
    best and worst performing funds, net new money this year, and top shareholders.
    Use partial name match (e.g. 'BTG' or 'LarrainVial').
    """
    result: dict = {}

    # Identity from mv_administradores
    result["identity"] = _rows("""
        SELECT rut, nombre, funds_fm, funds_fm_vigente, funds_fi, funds_fi_vigente, funds_total
        FROM mv_administradores
        WHERE nombre ILIKE :admin
        LIMIT 1
    """, {"admin": f"%{admin}%"})

    # Total AUM (FM)
    result["fm_aum"] = _rows("""
        SELECT
            ROUND(SUM(cd.patrimonio_neto)::numeric / 1e9, 2) AS aum_total_bn_clp,
            COUNT(DISTINCT cd.run_fondo)                     AS num_fondos,
            MAX(cd.fecha)                                    AS fecha
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE fm.razon_social_administradora ILIKE :admin
          AND cd.fecha = (SELECT MAX(fecha) FROM cartola_diaria)
    """, {"admin": f"%{admin}%"})

    # Market share (FM)
    total_aum = _scalar("SELECT SUM(patrimonio_neto) FROM cartola_diaria WHERE fecha = (SELECT MAX(fecha) FROM cartola_diaria)")
    admin_aum_row = _rows("""
        SELECT SUM(cd.patrimonio_neto) AS aum
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE fm.razon_social_administradora ILIKE :admin
          AND cd.fecha = (SELECT MAX(fecha) FROM cartola_diaria)
    """, {"admin": f"%{admin}%"})
    admin_aum = admin_aum_row[0]["aum"] if admin_aum_row and admin_aum_row[0]["aum"] else 0
    result["market_share_pct"] = round(float(admin_aum) / float(total_aum) * 100, 2) if total_aum and total_aum > 0 else None

    # Best performers
    result["best_funds"] = _rows("""
        SELECT run_fondo, serie, nombre_fondo, r_1y, r_ytd, valor_actual
        FROM mv_rentabilidad_fm
        WHERE administrador ILIKE :admin AND r_1y IS NOT NULL
        ORDER BY r_1y DESC NULLS LAST
        LIMIT 5
    """, {"admin": f"%{admin}%"})

    # Worst performers
    result["worst_funds"] = _rows("""
        SELECT run_fondo, serie, nombre_fondo, r_1y, r_ytd, valor_actual
        FROM mv_rentabilidad_fm
        WHERE administrador ILIKE :admin AND r_1y IS NOT NULL
        ORDER BY r_1y ASC NULLS LAST
        LIMIT 5
    """, {"admin": f"%{admin}%"})

    # Net new money YTD
    result["net_new_money_ytd"] = _rows("""
        SELECT
            ROUND(SUM(cd.monto_aportado)::numeric / 1e9, 2)     AS aportes_bn_clp,
            ROUND(SUM(cd.monto_rescatado)::numeric / 1e9, 2)    AS rescates_bn_clp,
            ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2) AS net_new_money_bn_clp
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE fm.razon_social_administradora ILIKE :admin
          AND cd.fecha >= DATE_TRUNC('year', CURRENT_DATE)
    """, {"admin": f"%{admin}%"})

    # Top shareholders (FI funds)
    result["top_shareholders"] = _rows("""
        SELECT
            COALESCE(a.nombre_canonical, a.nombre)  AS nombre,
            a.rut, a.tipo_persona,
            COUNT(DISTINCT a.run_fondo)              AS num_fondos,
            ROUND(AVG(a.pct_propiedad)::numeric, 2) AS avg_pct_propiedad
        FROM aportantes_fi a
        JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
        WHERE fi.administrador ILIKE :admin
          AND a.periodo = (
              SELECT MAX(x.periodo) FROM aportantes_fi x
              JOIN fondos_inversion g ON g.run_fondo = x.run_fondo
              WHERE g.administrador ILIKE :admin
          )
          AND a.rut IS NOT NULL
        GROUP BY COALESCE(a.nombre_canonical, a.nombre), a.rut, a.tipo_persona
        ORDER BY num_fondos DESC, avg_pct_propiedad DESC
        LIMIT 10
    """, {"admin": f"%{admin}%"})

    return result


@mcp.tool()
def compare_administrators(admin_a: str, admin_b: str) -> dict:
    """
    Side-by-side comparison of two administradoras.
    Returns AUM, fund counts, return performance, net new money, and shareholder overlap.
    Includes a merge scenario: combined AUM, shareholders gained by each side.
    Powerful for M&A analysis.
    """
    def _admin_stats(admin: str) -> dict:
        aum = _rows("""
            SELECT
                ROUND(SUM(cd.patrimonio_neto)::numeric / 1e9, 2) AS aum_bn_clp,
                COUNT(DISTINCT cd.run_fondo) AS fondos_vigentes
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE fm.razon_social_administradora ILIKE :admin
              AND cd.fecha = (SELECT MAX(fecha) FROM cartola_diaria)
              AND fm.fecha_termino_operaciones IS NULL
        """, {"admin": f"%{admin}%"})

        nnm = _rows("""
            SELECT ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2) AS net_new_money_ytd_bn
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE fm.razon_social_administradora ILIKE :admin
              AND cd.fecha >= DATE_TRUNC('year', CURRENT_DATE)
        """, {"admin": f"%{admin}%"})

        ret = _rows("""
            SELECT
                ROUND(AVG(r_1m)::numeric, 2) AS avg_r_1m,
                ROUND(AVG(r_1y)::numeric, 2) AS avg_r_1y,
                ROUND(AVG(r_ytd)::numeric, 2) AS avg_r_ytd
            FROM mv_rentabilidad_fm
            WHERE administrador ILIKE :admin
        """, {"admin": f"%{admin}%"})

        return {
            "aum": aum[0] if aum else {},
            "net_new_money_ytd": nnm[0] if nnm else {},
            "avg_returns": ret[0] if ret else {},
        }

    result = {
        "admin_a": {"name": admin_a, **_admin_stats(admin_a)},
        "admin_b": {"name": admin_b, **_admin_stats(admin_b)},
    }

    # Shareholder overlap (FI)
    overlap = _rows("""
        WITH holders_a AS (
            SELECT DISTINCT a.rut FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin_a AND a.rut IS NOT NULL
              AND a.periodo = (SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin_a)
        ),
        holders_b AS (
            SELECT DISTINCT a.rut FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin_b AND a.rut IS NOT NULL
              AND a.periodo = (SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin_b)
        )
        SELECT
            (SELECT COUNT(*) FROM holders_a)                        AS shareholders_a,
            (SELECT COUNT(*) FROM holders_b)                        AS shareholders_b,
            (SELECT COUNT(*) FROM holders_a INTERSECT SELECT rut FROM holders_b) AS shared,
            (SELECT COUNT(*) FROM holders_a EXCEPT SELECT rut FROM holders_b)    AS only_in_a,
            (SELECT COUNT(*) FROM holders_b EXCEPT SELECT rut FROM holders_a)    AS only_in_b
    """, {"admin_a": f"%{admin_a}%", "admin_b": f"%{admin_b}%"})

    result["shareholder_overlap"] = overlap[0] if overlap else {}

    # Top shared shareholders
    result["top_shared_shareholders"] = _rows("""
        WITH holders_a AS (
            SELECT DISTINCT a.rut, COALESCE(a.nombre_canonical, a.nombre) AS nombre
            FROM aportantes_fi a JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin_a AND a.rut IS NOT NULL
              AND a.periodo = (SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin_a)
        ),
        holders_b AS (
            SELECT DISTINCT a.rut FROM aportantes_fi a JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin_b AND a.rut IS NOT NULL
              AND a.periodo = (SELECT MAX(x.periodo) FROM aportantes_fi x JOIN fondos_inversion g ON g.run_fondo = x.run_fondo WHERE g.administrador ILIKE :admin_b)
        )
        SELECT a.rut, a.nombre FROM holders_a a
        WHERE a.rut IN (SELECT rut FROM holders_b)
        LIMIT 10
    """, {"admin_a": f"%{admin_a}%", "admin_b": f"%{admin_b}%"})

    return result


@mcp.tool()
def get_shareholder_positions(
    nombre: str | None = None,
    rut: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Track any institutional shareholder (AFP, bank, family office, etc.) across all
    investment funds and time periods. Shows total estimated AUM, evolution by quarter,
    and which administrators they work with.
    Use nombre for partial name match or rut for exact RUT match.
    """
    if not nombre and not rut:
        return {"error": "Provide either nombre or rut"}

    condition = "a.rut = :rut" if rut else "a.nombre_canonical ILIKE :nombre OR a.nombre ILIKE :nombre"
    params: dict = {"limit": limit}
    if rut:
        params["rut"] = rut
    else:
        params["nombre"] = f"%{nombre}%"

    # All positions across time
    positions = _rows(f"""
        SELECT
            a.periodo, a.run_fondo, f.razon_social AS fondo,
            f.administrador, a.rank, a.pct_propiedad,
            COALESCE(a.nombre_canonical, a.nombre) AS nombre_canonical
        FROM aportantes_fi a
        JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
        WHERE {condition}
        ORDER BY a.periodo DESC, a.pct_propiedad DESC NULLS LAST
        LIMIT :limit
    """, params)

    # Summary by admin (latest period)
    by_admin = _rows(f"""
        SELECT
            f.administrador,
            COUNT(DISTINCT a.run_fondo) AS num_fondos,
            ROUND(AVG(a.pct_propiedad)::numeric, 2) AS avg_pct,
            MAX(a.periodo) AS ultimo_periodo
        FROM aportantes_fi a
        JOIN fondos_inversion f ON f.run_fondo = a.run_fondo
        WHERE {condition}
        GROUP BY f.administrador
        ORDER BY num_fondos DESC
    """, params)

    # Evolution: how many funds per quarter
    evolution = _rows(f"""
        SELECT a.periodo, COUNT(DISTINCT a.run_fondo) AS num_fondos
        FROM aportantes_fi a
        WHERE {condition}
        GROUP BY a.periodo
        ORDER BY a.periodo DESC
        LIMIT 12
    """, params)

    return {
        "positions": positions,
        "by_administrator": by_admin,
        "quarterly_evolution": evolution,
    }


@mcp.tool()
def potential_clients(admin: str, limit: int = 20) -> list[dict]:
    """
    Find the largest institutional shareholders in the Chilean fund market that are NOT
    currently invested in this administrator's funds. These are potential clients.
    Ranked by number of funds they hold with other administrators.
    """
    return _rows("""
        WITH admin_holders AS (
            SELECT DISTINCT a.rut
            FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin AND a.rut IS NOT NULL
              AND a.periodo = (
                  SELECT MAX(x.periodo) FROM aportantes_fi x
                  JOIN fondos_inversion g ON g.run_fondo = x.run_fondo
                  WHERE g.administrador ILIKE :admin
              )
        ),
        all_holders AS (
            SELECT
                a.rut,
                COALESCE(a.nombre_canonical, a.nombre)  AS nombre,
                a.tipo_persona,
                COUNT(DISTINCT a.run_fondo)              AS total_funds_in_market,
                COUNT(DISTINCT fi.administrador)         AS num_admins,
                MAX(a.periodo)                           AS last_seen
            FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE a.rut IS NOT NULL
              AND a.periodo = (SELECT MAX(periodo) FROM aportantes_fi)
            GROUP BY a.rut, COALESCE(a.nombre_canonical, a.nombre), a.tipo_persona
        )
        SELECT h.rut, h.nombre, h.tipo_persona,
               h.total_funds_in_market, h.num_admins, h.last_seen
        FROM all_holders h
        WHERE h.rut NOT IN (SELECT rut FROM admin_holders)
        ORDER BY h.total_funds_in_market DESC
        LIMIT :limit
    """, {"admin": f"%{admin}%", "limit": limit})


@mcp.tool()
def market_overview() -> dict:
    """
    Full snapshot of the Chilean fund market: total AUM, top administrators by AUM,
    market concentration, best performing funds, and net new money leaders this month.
    Great starting point for any market analysis.
    """
    result: dict = {}

    # Total FM AUM
    result["total_fm_aum"] = _rows("""
        SELECT
            ROUND(SUM(patrimonio_neto)::numeric / 1e9, 2) AS total_aum_bn_clp,
            COUNT(DISTINCT run_fondo) AS num_fondos,
            MAX(fecha) AS fecha
        FROM cartola_diaria
        WHERE fecha = (SELECT MAX(fecha) FROM cartola_diaria)
    """)

    # Top 10 AGFs by AUM
    result["top_agfs_by_aum"] = _rows("""
        SELECT
            fm.razon_social_administradora AS administrador,
            ROUND(SUM(cd.patrimonio_neto)::numeric / 1e9, 2) AS aum_bn_clp,
            ROUND(SUM(cd.patrimonio_neto) * 100.0 / SUM(SUM(cd.patrimonio_neto)) OVER (), 2) AS market_share_pct,
            COUNT(DISTINCT cd.run_fondo) AS num_fondos
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE cd.fecha = (SELECT MAX(fecha) FROM cartola_diaria)
        GROUP BY fm.razon_social_administradora
        ORDER BY aum_bn_clp DESC NULLS LAST
        LIMIT 10
    """)

    # Net new money this month (top 5 + bottom 5)
    result["net_new_money_this_month"] = _rows("""
        SELECT
            fm.razon_social_administradora AS administrador,
            ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2) AS net_new_money_bn_clp
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE cd.fecha >= DATE_TRUNC('month', CURRENT_DATE)
          AND cd.monto_aportado IS NOT NULL
        GROUP BY fm.razon_social_administradora
        ORDER BY net_new_money_bn_clp DESC NULLS LAST
        LIMIT 10
    """)

    # Top 5 FM funds by 1Y return
    result["top_funds_1y"] = _rows("""
        SELECT run_fondo, nombre_fondo, administrador, r_1y, r_ytd, valor_actual
        FROM mv_rentabilidad_fm
        WHERE r_1y IS NOT NULL
        ORDER BY r_1y DESC NULLS LAST
        LIMIT 5
    """)

    # Top 5 FI funds by 1Y return
    result["top_fi_funds_1y"] = _rows("""
        SELECT r.run_fondo, f.razon_social AS nombre, r.administrador, r.r_1y, r.r_ytd
        FROM mv_rentabilidad_fi r
        LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
        WHERE r.r_1y IS NOT NULL
        ORDER BY r.r_1y DESC NULLS LAST
        LIMIT 5
    """)

    return result
