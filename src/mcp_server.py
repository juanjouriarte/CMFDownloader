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
- Portfolio tools use SII emisores data (96% match on FM, 73% on FI — unmatched are individual debtors by design)
- emisor_fund_exposure and top_emisores_in_market only cover domestic (NACI) portfolios; foreign positions lack RUT
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

# FI non-rescatable flows are quarterly (cuotas_fi.periodo = quarter-end date)
_FI_PERIOD_SQL = {
    "this_month":  "cf.periodo >= DATE_TRUNC('month', CURRENT_DATE)",
    "last_month":  "cf.periodo >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') AND cf.periodo < DATE_TRUNC('month', CURRENT_DATE)",
    "last_3m":     "cf.periodo >= CURRENT_DATE - INTERVAL '3 months'",
    "last_6m":     "cf.periodo >= CURRENT_DATE - INTERVAL '6 months'",
    "ytd":         "cf.periodo >= DATE_TRUNC('year', CURRENT_DATE)",
    "last_12m":    "cf.periodo >= CURRENT_DATE - INTERVAL '12 months'",
}

# FI rescatable: same date filter on valores_cuota_fi.fecha (now that flujo_neto is pre-computed)
_FI_RESCATABLE_PERIOD_SQL = {
    "this_month":  "v.fecha >= DATE_TRUNC('month', CURRENT_DATE)",
    "last_month":  "v.fecha >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') AND v.fecha < DATE_TRUNC('month', CURRENT_DATE)",
    "last_3m":     "v.fecha >= CURRENT_DATE - INTERVAL '3 months'",
    "last_6m":     "v.fecha >= CURRENT_DATE - INTERVAL '6 months'",
    "ytd":         "v.fecha >= DATE_TRUNC('year', CURRENT_DATE)",
    "last_12m":    "v.fecha >= CURRENT_DATE - INTERVAL '12 months'",
}

_SORT_COLS = {"r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"}

# fi_equity_activity helpers
# cuotas_pagadas and cuotas_emitidas are cumulative STOCKS (not flows).
# The signal is the delta vs the previous quarter via LAG().
# pending_calls looks at the current balance of the pipeline columns.
_ACTIVITY_FILTER_SQL = {
    "all":           "1=1",
    "raising":       "(delta_pagadas > 0 OR delta_emitidas > 0)",
    "returning":     "delta_pagadas < 0",
    "pending_calls": "(cuotas_suscritas_no_pagadas > 0 OR num_cuotas_promesa > 0)",
}

_ACTIVITY_ORDER_COL = {
    "all":           "ABS(capital_called_bn_clp)",
    "raising":       "capital_called_bn_clp",
    "returning":     "capital_called_bn_clp",   # DESC → most negative first
    "pending_calls": "(pending_formal_bn_clp + pending_promise_bn_clp)",
}

_ACTIVITY_HAVING_SQL = {
    "all":           "TRUE",
    "raising":       "(SUM(delta_pagadas) > 0 OR SUM(delta_emitidas) > 0)",
    "returning":     "SUM(delta_pagadas) < 0",
    "pending_calls": "(SUM(cuotas_suscritas_no_pagadas) > 0 OR SUM(num_cuotas_promesa) > 0)",
}


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
) -> dict:
    """
    Search for mutual funds (FM) or investment funds (FI) by name or administrator.
    Tolerant of partial names and typos — uses trigram similarity as fallback.
    Returns fund identity + latest returns plus the best administrator name match
    so you can pass it directly to get_administrator_full_picture or get_administrator_funds.
    """
    params = {"q": f"%{query}%", "limit": limit, "q_sim": query}
    vigente_fm = "AND fecha_termino_operaciones IS NULL" if vigente else ""
    vigente_fi = "AND fi.vigente = true" if vigente else ""

    type_filter_fm = "AND '1'='1'" if fund_type in ("fm", "all") else "AND '1'='0'"
    type_filter_fi = "AND '1'='1'" if fund_type in ("fi", "all") else "AND '1'='0'"

    results = _rows(f"""
        SELECT tipo, run_fondo, nombre, administrador, tipo_fondo, moneda,
               vigente, r_1m, r_1y, r_ytd, valor_cuota_actual, fecha_calculo
        FROM (
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
              {vigente_fm} {type_filter_fm}
            UNION ALL
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
              {vigente_fi} {type_filter_fi}
        ) combined
        ORDER BY nombre
        LIMIT :limit
    """, params)

    # If no results, try trigram similarity fallback
    if not results:
        similar = _rows("""
            SELECT nombre, administrador, tipo FROM (
                SELECT nombre_fondo AS nombre, razon_social_administradora AS administrador,
                       'fm' AS tipo,
                       SIMILARITY(nombre_fondo, :q_sim) AS sim
                FROM fondo_mutuo
                UNION ALL
                SELECT razon_social, administrador, 'fi',
                       SIMILARITY(razon_social, :q_sim)
                FROM fondos_inversion
            ) x
            WHERE sim > 0.1
            ORDER BY sim DESC
            LIMIT 5
        """, params)
        return {
            "results": [],
            "message": f"No exact matches for '{query}'.",
            "did_you_mean": [r["nombre"] for r in similar] if similar else [],
        }

    # Also surface the unique admin names found for easy follow-up calls
    admins = list({r["administrador"] for r in results if r["administrador"]})
    return {"results": results, "admins_found": admins}


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
                r.is_data_suspicious, r.suspicious_periods,
                cd.patrimonio_neto AS aum_clp
            FROM v_rentabilidad_fm_quality r
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
    as_of_date: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Ranking of best performing funds by return period. Returns one row per fund
    (the best-performing series), so BTG Litio+ appears once, not three times.
    fund_type: 'fm' for mutual funds, 'fi' for investment funds.
    sort_by: r_1d=1 day, r_1w=1 week, r_1m=1 month, r_1y=1 year, r_5y=5 years, r_ytd=year-to-date.
    as_of_date: optional YYYY-MM-DD — compute returns dynamically as of that date instead of today's MV.
    FM returns include factor_reparto (distributions). FI returns are NAV-only (no dividends) when as_of_date is set.
    """
    if sort_by not in _SORT_COLS:
        sort_by = "r_1y"

    admin_filter = "AND administrador ILIKE :admin" if admin else ""
    params: dict = {"limit": limit}
    if admin:
        params["admin"] = f"%{admin}%"

    # No as_of_date — use today's materialized view (fast path)
    if not as_of_date:
        if fund_type == "fm":
            return _rows(f"""
                SELECT run_fondo, serie, nombre_fondo, administrador,
                       valor_actual, fecha_calculo,
                       r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd,
                       is_data_suspicious, suspicious_periods
                FROM (
                    SELECT DISTINCT ON (run_fondo) run_fondo, serie, nombre_fondo, administrador,
                           valor_actual, fecha_calculo,
                           r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd,
                           is_data_suspicious, suspicious_periods
                    FROM v_rentabilidad_fm_quality
                    WHERE {sort_by} IS NOT NULL AND NOT is_data_suspicious {admin_filter}
                    ORDER BY run_fondo, {sort_by} DESC NULLS LAST
                ) best
                ORDER BY {sort_by} DESC NULLS LAST
                LIMIT :limit
            """, params)
        else:
            return _rows(f"""
                SELECT run_fondo, serie, nombre, administrador,
                       valor_actual, fecha_calculo,
                       r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd
                FROM (
                    SELECT DISTINCT ON (r.run_fondo)
                           r.run_fondo, r.serie, f.razon_social AS nombre, r.administrador,
                           r.valor_actual, r.fecha_calculo,
                           r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
                    FROM mv_rentabilidad_fi r
                    LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
                    WHERE r.{sort_by} IS NOT NULL {admin_filter}
                    ORDER BY r.run_fondo, r.{sort_by} DESC NULLS LAST
                ) best
                ORDER BY {sort_by} DESC NULLS LAST
                LIMIT :limit
            """, params)

    # Dynamic historical computation
    interval_sql = {
        "r_1d":  "INTERVAL '1 day'",
        "r_1w":  "INTERVAL '7 days'",
        "r_1m":  "INTERVAL '1 month'",
        "r_1y":  "INTERVAL '1 year'",
        "r_5y":  "INTERVAL '5 years'",
        "r_ytd": None,
    }[sort_by]

    start_sql = (
        f":as_of::date - {interval_sql}"
        if interval_sql
        else "DATE_TRUNC('year', :as_of::date)"
    )
    params["as_of"] = as_of_date

    if fund_type == "fm":
        admin_join_filter = "AND fm.razon_social_administradora ILIKE :admin" if admin else ""
        return _rows(f"""
            WITH end_ref AS (
                SELECT fecha FROM cartola_diaria
                WHERE fecha <= :as_of::date
                GROUP BY fecha ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
                LIMIT 1
            ),
            start_ref AS (
                SELECT fecha FROM cartola_diaria
                WHERE fecha <= {start_sql}
                GROUP BY fecha ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
                LIMIT 1
            ),
            end_nav AS (
                SELECT run_fondo, serie, valor_cuota, fecha
                FROM cartola_diaria WHERE fecha = (SELECT fecha FROM end_ref)
            ),
            start_nav AS (
                SELECT run_fondo, serie, valor_cuota
                FROM cartola_diaria WHERE fecha = (SELECT fecha FROM start_ref)
            ),
            dist AS (
                SELECT run_fondo, serie,
                       EXP(SUM(LN(factor_reparto))) AS cum_factor
                FROM cartola_diaria
                WHERE fecha >  (SELECT fecha FROM start_ref)
                  AND fecha <= (SELECT fecha FROM end_ref)
                  AND factor_reparto > 0 AND factor_reparto IS NOT NULL
                GROUP BY run_fondo, serie
            )
            SELECT run_fondo, serie, nombre_fondo, administrador,
                   valor_actual, fecha_calculo, retorno
            FROM (
                SELECT DISTINCT ON (e.run_fondo)
                    e.run_fondo, e.serie,
                    fm.nombre_fondo,
                    fm.razon_social_administradora AS administrador,
                    e.valor_cuota                  AS valor_actual,
                    e.fecha                        AS fecha_calculo,
                    ROUND((e.valor_cuota * COALESCE(d.cum_factor, 1)
                           / NULLIF(s.valor_cuota, 0) - 1) * 100, 4) AS retorno
                FROM end_nav e
                JOIN start_nav s ON s.run_fondo = e.run_fondo AND s.serie = e.serie
                LEFT JOIN dist d ON d.run_fondo = e.run_fondo AND d.serie = e.serie
                JOIN fondo_mutuo fm ON fm.run_fondo = e.run_fondo
                WHERE s.valor_cuota > 0 {admin_join_filter}
                ORDER BY e.run_fondo,
                         ROUND((e.valor_cuota * COALESCE(d.cum_factor, 1)
                                / NULLIF(s.valor_cuota, 0) - 1) * 100, 4) DESC NULLS LAST
            ) best
            ORDER BY retorno DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        admin_join_filter = "AND fi.administrador ILIKE :admin" if admin else ""
        return _rows(f"""
            WITH end_ref AS (
                SELECT fecha FROM valores_cuota_fi
                WHERE fecha <= :as_of::date
                GROUP BY fecha ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
                LIMIT 1
            ),
            start_ref AS (
                SELECT fecha FROM valores_cuota_fi
                WHERE fecha <= {start_sql}
                GROUP BY fecha ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
                LIMIT 1
            ),
            end_nav AS (
                SELECT run_fondo, serie, valor_libro, fecha
                FROM valores_cuota_fi WHERE fecha = (SELECT fecha FROM end_ref)
            ),
            start_nav AS (
                SELECT run_fondo, serie, valor_libro
                FROM valores_cuota_fi WHERE fecha = (SELECT fecha FROM start_ref)
            )
            SELECT run_fondo, serie, nombre, administrador,
                   valor_actual, fecha_calculo, retorno
            FROM (
                SELECT DISTINCT ON (e.run_fondo)
                    e.run_fondo, e.serie,
                    fi.razon_social AS nombre,
                    fi.administrador,
                    e.valor_libro   AS valor_actual,
                    e.fecha         AS fecha_calculo,
                    ROUND((e.valor_libro - s.valor_libro)
                          / NULLIF(s.valor_libro, 0) * 100, 4) AS retorno
                FROM end_nav e
                JOIN start_nav s ON s.run_fondo = e.run_fondo AND s.serie = e.serie
                JOIN fondos_inversion fi ON fi.run_fondo = e.run_fondo
                WHERE s.valor_libro > 0 {admin_join_filter}
                ORDER BY e.run_fondo,
                         ROUND((e.valor_libro - s.valor_libro)
                               / NULLIF(s.valor_libro, 0) * 100, 4) DESC NULLS LAST
            ) best
            ORDER BY retorno DESC NULLS LAST
            LIMIT :limit
        """, params)


@mcp.tool()
def net_new_money_ranking(
    period: Literal["this_month", "last_month", "last_3m", "last_6m", "ytd", "last_12m"] = "this_month",
    group_by: Literal["agf", "fund"] = "agf",
    fund_type: Literal["fm", "fi"] = "fm",
    rescatable: bool | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Rank AGFs or individual funds by net new money (aportes - rescates).
    Positive = attracted capital. Negative = net outflows.
    fund_type: 'fm' (mutual funds, daily) or 'fi' (investment funds).
    rescatable: FI only — True=rescatable, False=non-rescatable, None=both shown as separate rows.
    FI method differs by type:
      - Non-rescatable: quarter-over-quarter delta of cuotas_pagadas (capital actually called
        from investors) × valor_libro from quarterly cuotas_fi.
      - Rescatable: implied daily flows — cuotas = patrimonio_neto / valor_libro;
        NNM = (cuotas_end - cuotas_start) × valor_libro_end. Strips performance from AUM change.
    from_date / to_date (YYYY-MM-DD): custom date range — overrides period when provided.
    period: for non-rescatable FI, last_3m/ytd/last_12m work best (quarterly data).
    """
    params: dict = {"limit": limit}

    if fund_type == "fi":
        results = []
        if rescatable is not False:
            results += _fi_nnm_rescatable(period, group_by, from_date, to_date, params.copy())
        if rescatable is not True:
            results += _fi_nnm_non_rescatable(period, group_by, from_date, to_date, params.copy())
        results.sort(key=lambda r: (r.get("net_new_money_bn_clp") or 0), reverse=True)
        return results[:limit]

    # FM — daily cartola_diaria
    if from_date or to_date:
        conditions = ["cd.monto_aportado IS NOT NULL"]
        if from_date:
            conditions.append("cd.fecha >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("cd.fecha <= :to_date")
            params["to_date"] = to_date
        period_filter = " AND ".join(conditions)
    else:
        period_filter = _PERIOD_SQL[period] + " AND cd.monto_aportado IS NOT NULL"

    if group_by == "agf":
        return _rows(f"""
            SELECT
                fm.razon_social_administradora                                          AS administrador,
                ROUND(SUM(cd.monto_aportado)::numeric / 1e9, 2)                        AS aportes_bn_clp,
                ROUND(SUM(cd.monto_rescatado)::numeric / 1e9, 2)                       AS rescates_bn_clp,
                ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2)   AS net_new_money_bn_clp,
                COUNT(DISTINCT cd.run_fondo)                                            AS num_fondos,
                MIN(cd.fecha)                                                           AS desde,
                MAX(cd.fecha)                                                           AS hasta
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE {period_filter}
            GROUP BY fm.razon_social_administradora
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            SELECT
                cd.run_fondo,
                fm.nombre_fondo,
                fm.razon_social_administradora                                          AS administrador,
                ROUND(SUM(cd.monto_aportado)::numeric / 1e9, 2)                        AS aportes_bn_clp,
                ROUND(SUM(cd.monto_rescatado)::numeric / 1e9, 2)                       AS rescates_bn_clp,
                ROUND(SUM(cd.monto_aportado - cd.monto_rescatado)::numeric / 1e9, 2)   AS net_new_money_bn_clp,
                MIN(cd.fecha)                                                           AS desde,
                MAX(cd.fecha)                                                           AS hasta
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE {period_filter}
            GROUP BY cd.run_fondo, fm.nombre_fondo, fm.razon_social_administradora
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)


def _fi_nnm_non_rescatable(
    period: str,
    group_by: str,
    from_date: str | None,
    to_date: str | None,
    params: dict,
) -> list[dict]:
    """
    FI non-rescatable NNM: quarter-over-quarter delta of cuotas_pagadas (capital actually
    paid in by investors, not just authorized via cuotas_emitidas) × valor_libro.
    cuotas_pagadas is a cumulative STOCK, so the delta is computed via LAG() over each
    fund's full history *before* the period filter is applied — otherwise the first quarter
    inside the window would wrongly show its entire cumulative stock as a one-quarter inflow.
    """
    if from_date or to_date:
        conditions = []
        if from_date:
            conditions.append("d.periodo >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("d.periodo <= :to_date")
            params["to_date"] = to_date
        period_filter = " AND ".join(conditions) if conditions else "TRUE"
    else:
        period_filter = _FI_PERIOD_SQL[period].replace("cf.periodo", "d.periodo")

    deltas_cte = """
        WITH history AS (
            SELECT cf.run_fondo, cf.periodo, cf.valor_libro,
                   COALESCE(cf.cuotas_pagadas, 0) AS cuotas_pagadas,
                   LAG(COALESCE(cf.cuotas_pagadas, 0)) OVER (PARTITION BY cf.run_fondo ORDER BY cf.periodo) AS prev_pagadas
            FROM cuotas_fi cf
            WHERE cf.valor_libro IS NOT NULL AND cf.valor_libro > 0
        ),
        deltas AS (
            SELECT run_fondo, periodo,
                   (cuotas_pagadas - COALESCE(prev_pagadas, cuotas_pagadas)) * valor_libro AS capital_called_clp
            FROM history
        )
    """

    if group_by == "agf":
        return _rows(f"""
            {deltas_cte}
            SELECT
                fi.administrador,
                false::boolean                                          AS rescatable,
                NULL::numeric                                           AS aportes_bn_clp,
                NULL::numeric                                           AS rescates_bn_clp,
                ROUND(SUM(d.capital_called_clp) / 1e9, 2)              AS net_new_money_bn_clp,
                COUNT(DISTINCT d.run_fondo)                             AS num_fondos,
                MIN(d.periodo)                                          AS desde,
                MAX(d.periodo)                                          AS hasta
            FROM deltas d
            JOIN fondos_inversion fi ON fi.run_fondo = d.run_fondo
            WHERE {period_filter} AND fi.rescatable = false
            GROUP BY fi.administrador
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            {deltas_cte}
            SELECT
                d.run_fondo,
                fi.razon_social                                         AS nombre_fondo,
                fi.administrador,
                false::boolean                                          AS rescatable,
                NULL::numeric                                           AS aportes_bn_clp,
                NULL::numeric                                           AS rescates_bn_clp,
                ROUND(SUM(d.capital_called_clp) / 1e9, 2)              AS net_new_money_bn_clp,
                MIN(d.periodo)                                          AS desde,
                MAX(d.periodo)                                          AS hasta
            FROM deltas d
            JOIN fondos_inversion fi ON fi.run_fondo = d.run_fondo
            WHERE {period_filter} AND fi.rescatable = false
            GROUP BY d.run_fondo, fi.razon_social, fi.administrador
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)


def _fi_nnm_rescatable(
    period: str,
    group_by: str,
    from_date: str | None,
    to_date: str | None,
    params: dict,
) -> list[dict]:
    """
    FI rescatable NNM: SUM(flujo_neto) over the period.
    flujo_neto is pre-computed daily in valores_cuota_fi as:
      (cuotas_t - cuotas_{t-1}) * valor_libro_t  where cuotas = patrimonio_neto / valor_libro.
    Same pattern as FM monto_aportado/monto_rescatado — just a filtered aggregate.
    """
    if from_date or to_date:
        conditions = ["v.flujo_neto IS NOT NULL"]
        if from_date:
            conditions.append("v.fecha >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("v.fecha <= :to_date")
            params["to_date"] = to_date
        period_filter = " AND ".join(conditions)
    else:
        period_filter = _FI_RESCATABLE_PERIOD_SQL[period] + " AND v.flujo_neto IS NOT NULL"

    if group_by == "agf":
        return _rows(f"""
            SELECT
                fi.administrador,
                true::boolean                                        AS rescatable,
                NULL::numeric                                        AS aportes_bn_clp,
                NULL::numeric                                        AS rescates_bn_clp,
                ROUND(SUM(v.flujo_neto) / 1e9, 2)                   AS net_new_money_bn_clp,
                COUNT(DISTINCT v.run_fondo)                         AS num_fondos,
                MIN(v.fecha)                                         AS desde,
                MAX(v.fecha)                                         AS hasta
            FROM valores_cuota_fi v
            JOIN fondos_inversion fi ON fi.run_fondo = v.run_fondo
            WHERE {period_filter}
              AND fi.rescatable = true
            GROUP BY fi.administrador
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            SELECT
                v.run_fondo,
                fi.razon_social                                      AS nombre_fondo,
                fi.administrador,
                true::boolean                                        AS rescatable,
                NULL::numeric                                        AS aportes_bn_clp,
                NULL::numeric                                        AS rescates_bn_clp,
                ROUND(SUM(v.flujo_neto) / 1e9, 2)                   AS net_new_money_bn_clp,
                MIN(v.fecha)                                         AS desde,
                MAX(v.fecha)                                         AS hasta
            FROM valores_cuota_fi v
            JOIN fondos_inversion fi ON fi.run_fondo = v.run_fondo
            WHERE {period_filter}
              AND fi.rescatable = true
            GROUP BY v.run_fondo, fi.razon_social, fi.administrador
            ORDER BY net_new_money_bn_clp DESC NULLS LAST
            LIMIT :limit
        """, params)


@mcp.tool()
def get_fund_full_picture(
    run_fondo: str | None = None,
    fund_type: Literal["fm", "fi"] = "fm",
    nombre: str | None = None,
) -> dict:
    """
    Complete profile of a single fund: identity, all return periods, AUM trend (last 12 months),
    net new money (FM only), top shareholders (FI), top portfolio positions, and instrument summary.
    Pass run_fondo (exact) OR nombre (partial name — resolves automatically).
    If nombre matches multiple funds, returns a list of candidates to pick from.
    """
    # Resolve run_fondo from name if not provided
    if not run_fondo and nombre:
        if fund_type == "fm":
            row = _rows("""
                SELECT run_fondo FROM fondo_mutuo
                WHERE nombre_fondo ILIKE :q OR nombre_corto ILIKE :q
                ORDER BY fecha_termino_operaciones IS NOT NULL, nombre_fondo
                LIMIT 5
            """, {"q": f"%{nombre}%"})
        else:
            row = _rows("""
                SELECT run_fondo FROM fondos_inversion
                WHERE razon_social ILIKE :q
                ORDER BY vigente DESC NULLS LAST, razon_social
                LIMIT 5
            """, {"q": f"%{nombre}%"})
        if not row:
            return {"error": f"No fund found matching '{nombre}'"}
        if len(row) > 1:
            return {"candidates": [r["run_fondo"] for r in row],
                    "message": f"Multiple funds match '{nombre}'. Pass run_fondo directly."}
        run_fondo = row[0]["run_fondo"]
    elif not run_fondo:
        return {"error": "Provide either run_fondo or nombre"}

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

        # Portfolio summary (latest quarter) — by instrument type
        result["portfolio_summary"] = _rows("""
            SELECT tipo_instrumento,
                   COUNT(*) AS posiciones,
                   ROUND(SUM(CAST(NULLIF(porcentaje_activos_fondo,'') AS numeric))::numeric, 2) AS pct_total
            FROM cartera_naci
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run)
            GROUP BY tipo_instrumento
            ORDER BY pct_total DESC NULLS LAST
        """, {"run": run_fondo})

        # Top 10 positions enriched with SII + fund names
        result["top_positions"] = _rows("""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_naci WHERE run_fondo = :run)
            SELECT c.nemotecnico,
                   COALESCE(e.razon_social, fm.nombre_fondo, fi.razon_social) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi.razon_social)                 AS nombre_fondo_emisor,
                   c.tipo_instrumento,
                   CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric)     AS pct_activo_fondo,
                   c.clasificacion_riesgo, c.tir, c.fecha_vencimiento
            FROM cartera_naci c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
            LEFT JOIN fondos_inversion fi ON fi.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = (SELECT t FROM latest)
            ORDER BY pct_activo_fondo DESC NULLS LAST
            LIMIT 10
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

        # Top 10 portfolio positions enriched with SII + fund names
        result["top_positions"] = _rows("""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_fi_nac WHERE run_fondo = :run)
            SELECT c.nemotecnico,
                   COALESCE(e.razon_social, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi2.razon_social)                 AS nombre_fondo_emisor,
                   c.tipo_instrumento, c.pct_activo_fondo,
                   c.clasif_riesgo, c.tir_val_par_precio, c.fecha_vencimiento
            FROM cartera_fi_nac c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
            LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = (SELECT t FROM latest)
            ORDER BY c.pct_activo_fondo DESC NULLS LAST
            LIMIT 10
        """, {"run": run_fondo})

    return result


@mcp.tool()
def get_administrator_full_picture(admin: str) -> dict:
    """
    Complete administradora profile: fund counts, total AUM, market share,
    best and worst performing funds, net new money this year, top shareholders,
    and top FM portfolio positions.
    Use partial name match — tolerant of abbreviations (e.g. 'BTG', 'Larrain', 'Banchile').
    If the name doesn't match, returns a list of all administrators to choose from.
    """
    # Check if admin name matches — return all admins as hint if not
    identity = _rows("""
        SELECT rut, nombre, funds_fm, funds_fm_vigente, funds_fi, funds_fi_vigente, funds_total
        FROM mv_administradores
        WHERE nombre ILIKE :admin
        LIMIT 1
    """, {"admin": f"%{admin}%"})

    if not identity:
        all_admins = _rows("SELECT nombre FROM mv_administradores ORDER BY nombre", {})
        return {
            "error": f"No administrator found matching '{admin}'.",
            "available_administrators": [r["nombre"] for r in all_admins],
        }

    result: dict = {}
    result["identity"] = identity

    # Total AUM (FM) — use most recent date where this admin has data
    result["fm_aum"] = _rows("""
        SELECT
            ROUND(SUM(cd.patrimonio_neto)::numeric / 1e9, 2) AS aum_total_bn_clp,
            COUNT(DISTINCT cd.run_fondo)                     AS num_fondos,
            MAX(cd.fecha)                                    AS fecha
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE fm.razon_social_administradora ILIKE :admin
          AND cd.fecha = (
              SELECT MAX(cd2.fecha) FROM cartola_diaria cd2
              JOIN fondo_mutuo fm2 ON fm2.run_fondo = cd2.run_fondo
              WHERE fm2.razon_social_administradora ILIKE :admin
          )
    """, {"admin": f"%{admin}%"})

    # Market share (FM)
    market_share_row = _rows("""
        WITH ref AS (
            SELECT fecha FROM cartola_diaria
            WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
            GROUP BY fecha ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
            LIMIT 1
        ),
        totals AS (
            SELECT
                fm.razon_social_administradora AS administrador,
                SUM(cd.patrimonio_neto) AS aum
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE cd.fecha = (SELECT fecha FROM ref)
            GROUP BY fm.razon_social_administradora
        )
        SELECT
            ROUND(SUM(aum) FILTER (WHERE administrador ILIKE :admin)::numeric / 1e9, 2) AS admin_aum_bn,
            ROUND(SUM(aum) FILTER (WHERE administrador ILIKE :admin) * 100.0 / NULLIF(SUM(aum), 0), 2) AS market_share_pct
        FROM totals
    """, {"admin": f"%{admin}%"})
    result["market_share_pct"] = market_share_row[0]["market_share_pct"] if market_share_row else None

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

    # Top FM portfolio positions across all admin's funds (latest quarter)
    result["top_fm_positions"] = _rows("""
        WITH latest AS (
            SELECT MAX(c.periodo) AS t
            FROM cartera_naci c
            JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo
            WHERE fm.razon_social_administradora ILIKE :admin
        )
        SELECT
            c.nemotecnico,
            COALESCE(e.razon_social, n_fm.nombre_fondo, n_fi.razon_social) AS nombre_emisor,
            COALESCE(n_fm.nombre_fondo, n_fi.razon_social)                 AS nombre_fondo_emisor,
            c.tipo_instrumento,
            COUNT(DISTINCT c.run_fondo)                                    AS num_fondos,
            ROUND(AVG(CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric))::numeric, 4) AS avg_pct_fondo,
            ROUND(SUM(CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric))::numeric, 2) AS sum_pct_across_funds
        FROM cartera_naci c
        JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo
        LEFT JOIN emisores e ON e.rut = c.rut_emisor
        LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
        LEFT JOIN fondo_mutuo n_fm ON n_fm.run_fondo = n.run_fondo
        LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
        LEFT JOIN fondos_inversion n_fi ON n_fi.run_fondo = nfi.run_fondo
        WHERE fm.razon_social_administradora ILIKE :admin
          AND c.periodo = (SELECT t FROM latest)
        GROUP BY c.nemotecnico, e.razon_social, n_fm.nombre_fondo, n_fi.razon_social, c.tipo_instrumento
        ORDER BY sum_pct_across_funds DESC NULLS LAST
        LIMIT 15
    """, {"admin": f"%{admin}%"})

    return result


@mcp.tool()
def get_administrator_funds(admin: str, vigente: bool = True) -> dict:
    """
    All funds (FM + FI) for an administrator with their latest NAV, AUM, and return metrics.
    Use this as a dashboard snapshot of all an admin's funds before drilling into a specific one.
    Use partial name match (e.g. 'BTG', 'Larrain', 'Banchile').
    If the name doesn't match, returns the list of all administrators.
    vigente: True (default) returns only active funds.
    """
    # Validate admin name
    check = _rows("""
        SELECT nombre FROM mv_administradores WHERE nombre ILIKE :admin LIMIT 1
    """, {"admin": f"%{admin}%"})

    if not check:
        all_admins = _rows("SELECT nombre FROM mv_administradores ORDER BY nombre", {})
        return {
            "error": f"No administrator found matching '{admin}'.",
            "available_administrators": [r["nombre"] for r in all_admins],
        }

    vigente_fm = "AND fm.fecha_termino_operaciones IS NULL" if vigente else ""
    vigente_fi = "AND fi.vigente = true" if vigente else ""

    fm_funds = _rows(f"""
        SELECT
            'fm'                                        AS tipo,
            fm.run_fondo,
            fm.nombre_fondo                             AS nombre,
            fm.tipo_fondo,
            fm.moneda,
            r.serie                                     AS mejor_serie,
            r.valor_actual                              AS valor_cuota,
            r.fecha_calculo,
            r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd,
            cd.patrimonio_neto                          AS aum_clp
        FROM fondo_mutuo fm
        LEFT JOIN (
            SELECT DISTINCT ON (run_fondo) run_fondo, serie, valor_actual, fecha_calculo,
                   r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd
            FROM mv_rentabilidad_fm
            ORDER BY run_fondo, r_1y DESC NULLS LAST
        ) r ON r.run_fondo = fm.run_fondo
        LEFT JOIN LATERAL (
            SELECT patrimonio_neto FROM cartola_diaria
            WHERE run_fondo = fm.run_fondo
            ORDER BY fecha DESC LIMIT 1
        ) cd ON true
        WHERE fm.razon_social_administradora ILIKE :admin {vigente_fm}
        ORDER BY aum_clp DESC NULLS LAST
    """, {"admin": f"%{admin}%"})

    fi_funds = _rows(f"""
        SELECT
            'fi'                                        AS tipo,
            fi.run_fondo,
            fi.razon_social                             AS nombre,
            NULL                                        AS tipo_fondo,
            NULL                                        AS moneda,
            r.serie                                     AS mejor_serie,
            r.valor_actual                              AS valor_cuota,
            r.fecha_calculo,
            r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd,
            vc.patrimonio_neto                          AS aum_clp
        FROM fondos_inversion fi
        LEFT JOIN (
            SELECT DISTINCT ON (run_fondo) run_fondo, serie, valor_actual, fecha_calculo,
                   r_1d, r_1w, r_1m, r_1y, r_5y, r_ytd
            FROM mv_rentabilidad_fi
            ORDER BY run_fondo, r_1y DESC NULLS LAST
        ) r ON r.run_fondo = fi.run_fondo
        LEFT JOIN LATERAL (
            SELECT patrimonio_neto FROM valores_cuota_fi
            WHERE run_fondo = fi.run_fondo
            ORDER BY fecha DESC LIMIT 1
        ) vc ON true
        WHERE fi.administrador ILIKE :admin {vigente_fi}
        ORDER BY aum_clp DESC NULLS LAST
    """, {"admin": f"%{admin}%"})

    return {
        "administrador": check[0]["nombre"],
        "fm_funds": fm_funds,
        "fi_funds": fi_funds,
        "total_fm": len(fm_funds),
        "total_fi": len(fi_funds),
    }


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
              AND cd.fecha = (
                  SELECT MAX(cd2.fecha) FROM cartola_diaria cd2
                  JOIN fondo_mutuo fm2 ON fm2.run_fondo = cd2.run_fondo
                  WHERE fm2.razon_social_administradora ILIKE :admin
              )
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
            (SELECT COUNT(*) FROM holders_a)                                                     AS shareholders_a,
            (SELECT COUNT(*) FROM holders_b)                                                     AS shareholders_b,
            (SELECT COUNT(*) FROM (SELECT rut FROM holders_a INTERSECT SELECT rut FROM holders_b) x) AS shared,
            (SELECT COUNT(*) FROM (SELECT rut FROM holders_a EXCEPT    SELECT rut FROM holders_b) x) AS only_in_a,
            (SELECT COUNT(*) FROM (SELECT rut FROM holders_b EXCEPT    SELECT rut FROM holders_a) x) AS only_in_b
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
        WITH ref_period AS (
            -- Use the most recent quarter where BOTH the admin and the broader
            -- market have published data, so both sides use the same snapshot.
            SELECT MAX(a.periodo) AS t
            FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin
        ),
        admin_holders AS (
            SELECT DISTINCT a.rut
            FROM aportantes_fi a
            JOIN fondos_inversion fi ON fi.run_fondo = a.run_fondo
            WHERE fi.administrador ILIKE :admin AND a.rut IS NOT NULL
              AND a.periodo = (SELECT t FROM ref_period)
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
              AND a.periodo = (SELECT t FROM ref_period)
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

    # Use most-populated date in last 7 days (same logic as rentability MVs)
    _ref_date_sql = """
        SELECT fecha FROM cartola_diaria
        WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
        GROUP BY fecha
        ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
        LIMIT 1
    """

    # Total FM AUM
    result["total_fm_aum"] = _rows(f"""
        SELECT
            ROUND(SUM(patrimonio_neto)::numeric / 1e9, 2) AS total_aum_bn_clp,
            COUNT(DISTINCT run_fondo) AS num_fondos,
            MAX(fecha) AS fecha
        FROM cartola_diaria
        WHERE fecha = ({_ref_date_sql})
    """)

    # Top 10 AGFs by AUM
    result["top_agfs_by_aum"] = _rows(f"""
        SELECT
            fm.razon_social_administradora AS administrador,
            ROUND(SUM(cd.patrimonio_neto)::numeric / 1e9, 2) AS aum_bn_clp,
            ROUND(SUM(cd.patrimonio_neto) * 100.0 / SUM(SUM(cd.patrimonio_neto)) OVER (), 2) AS market_share_pct,
            COUNT(DISTINCT cd.run_fondo) AS num_fondos
        FROM cartola_diaria cd
        JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
        WHERE cd.fecha = ({_ref_date_sql})
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


@mcp.tool()
def get_fund_portfolio(
    run_fondo: str,
    fund_type: Literal["fm", "fi"] = "fm",
    periodo: str | None = None,
) -> dict:
    """
    Full portfolio positions for a fund (FM or FI) for the latest or a specific quarter.
    Returns all positions enriched with SII company names plus a summary by instrument type.
    periodo format: 'YYYY-MM-DD' (quarter-end date) — omit for latest available.
    Use search_funds first to find run_fondo values.
    """
    result: dict = {}

    if fund_type == "fm":
        if not periodo:
            periodo = _scalar(
                "SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run",
                {"run": run_fondo},
            )
        if not periodo:
            return {"error": "No portfolio data found for this fund"}
        result["periodo"] = str(periodo)

        result["naci"] = _rows("""
            SELECT c.nemotecnico, c.rut_emisor,
                   COALESCE(e.razon_social, fm.nombre_fondo, fi.razon_social, c.rut_emisor) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi.razon_social) AS nombre_fondo_emisor,
                   c.tipo_instrumento,
                   CAST(NULLIF(c.porcentaje_activos_fondo, '') AS numeric) AS pct_activo_fondo,
                   CAST(NULLIF(c.valorizacion_cierre, '')      AS numeric) AS valorizacion_cierre,
                   c.clasificacion_riesgo, c.tir, c.fecha_vencimiento,
                   c.cantidad_unidades, c.tipo_unidades, c.moneda_liquidacion,
                   c.porcentaje_valor_par, c.tipo_interes,
                   c.codigo_pais_emisor, c.situacion_instrumento,
                   c.porcentaje_capital_emisor, c.porcentaje_activos_emisor,
                   c.codigo_grupo_empresarial
            FROM cartera_naci c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
            LEFT JOIN fondos_inversion fi ON fi.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = :periodo
            ORDER BY CAST(NULLIF(c.porcentaje_activos_fondo, '') AS numeric) DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

        result["extr"] = _rows("""
            SELECT c.nemotecnico,
                   COALESCE(c.nombre_emisor, fm.nombre_fondo, fi.razon_social) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi.razon_social) AS nombre_fondo_emisor,
                   c.tipo_instrumento,
                   CAST(NULLIF(c.porcentaje_activos_fondo, '') AS numeric) AS pct_activo_fondo,
                   CAST(NULLIF(c.valorizacion_cierre, '')      AS numeric) AS valorizacion_cierre,
                   c.clasificacion_riesgo, c.tir, c.fecha_vencimiento,
                   c.tipo_unidades, c.codigo_pais_emisor, c.situacion_instrumento,
                   c.nombre_grupo_empresarial
            FROM cartera_extr c
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
            LEFT JOIN fondos_inversion fi ON fi.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = :periodo
            ORDER BY CAST(NULLIF(c.porcentaje_activos_fondo, '') AS numeric) DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

        result["by_tipo_instrumento"] = _rows("""
            SELECT tipo_instrumento,
                   COUNT(*) AS posiciones,
                   ROUND(SUM(CAST(NULLIF(porcentaje_activos_fondo, '') AS numeric))::numeric, 2) AS pct_total_fondo
            FROM cartera_naci
            WHERE run_fondo = :run AND periodo = :periodo
            GROUP BY tipo_instrumento
            ORDER BY pct_total_fondo DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

    else:
        if not periodo:
            periodo = _scalar(
                "SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run",
                {"run": run_fondo},
            )
        if not periodo:
            return {"error": "No portfolio data found for this fund"}
        result["periodo"] = str(periodo)

        result["naci"] = _rows("""
            SELECT c.nemotecnico, c.rut_emisor,
                   COALESCE(e.razon_social, fm.nombre_fondo, fi2.razon_social, c.rut_emisor) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor,
                   c.tipo_instrumento, c.pct_activo_fondo,
                   c.valorizacion_cierre, c.clasif_riesgo,
                   c.tir_val_par_precio, c.fecha_vencimiento,
                   c.cant_unidades, c.tipo_unidades, c.cod_moneda_liquidacion,
                   c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                   c.situacion_instrumento, c.clasif_esf, c.cod_pais
            FROM cartera_fi_nac c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
            LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = :periodo
            ORDER BY c.pct_activo_fondo DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

        result["extr"] = _rows("""
            SELECT c.nemo_isin AS nemotecnico,
                   COALESCE(c.nombre_emisor, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                   COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor,
                   c.tipo_instrumento, c.pct_activo_fondo,
                   c.valorizacion_cierre, c.clasif_riesgo,
                   c.tir_val_par_precio, c.fecha_vencimiento,
                   c.cant_unidades, c.tipo_unidades, c.cod_moneda_liquidacion,
                   c.tipo_interes, c.pct_capital_emisor, c.pct_activo_emisor,
                   c.situacion_instrumento, c.clasif_esf, c.cod_pais
            FROM cartera_fi_ext c
            LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemo_isin
            LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
            LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemo_isin
            LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
            WHERE c.run_fondo = :run AND c.periodo = :periodo
            ORDER BY c.pct_activo_fondo DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

        result["by_tipo_instrumento"] = _rows("""
            SELECT tipo_instrumento,
                   COUNT(*) AS posiciones,
                   ROUND(SUM(pct_activo_fondo)::numeric, 2) AS pct_total_fondo
            FROM cartera_fi_nac
            WHERE run_fondo = :run AND periodo = :periodo
            GROUP BY tipo_instrumento
            ORDER BY pct_total_fondo DESC NULLS LAST
        """, {"run": run_fondo, "periodo": periodo})

    return result


@mcp.tool()
def top_emisores_in_market(
    fund_type: Literal["fm", "fi"] = "fm",
    tipo_instrumento: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """
    Ranking of companies (emisores) by how many funds hold them and total portfolio weight.
    Reveals market-wide concentration in specific issuers.
    fund_type: 'fm' uses cartera_naci, 'fi' uses cartera_fi_nac (domestic only).
    tipo_instrumento: optional filter, e.g. 'ACCION', 'BONO', 'DEPOSITO'.
    """
    tipo_filter = "AND c.tipo_instrumento ILIKE :tipo" if tipo_instrumento else ""
    params: dict = {"limit": limit}
    if tipo_instrumento:
        params["tipo"] = f"%{tipo_instrumento}%"

    if fund_type == "fm":
        return _rows(f"""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_naci)
            SELECT
                c.rut_emisor,
                COALESCE(e.razon_social, c.rut_emisor)                         AS nombre_emisor,
                COUNT(DISTINCT c.run_fondo)                                    AS num_fondos,
                COUNT(DISTINCT fm.razon_social_administradora)                 AS num_admins,
                ROUND(AVG(CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric))::numeric, 4) AS avg_pct_fondo,
                ROUND(SUM(CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric))::numeric, 2) AS sum_pct_across_funds,
                STRING_AGG(DISTINCT c.tipo_instrumento, ', ')                  AS tipos_instrumento
            FROM cartera_naci c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo
            WHERE c.periodo = (SELECT t FROM latest)
              AND c.rut_emisor IS NOT NULL
              {tipo_filter}
            GROUP BY c.rut_emisor, e.razon_social
            ORDER BY num_fondos DESC, sum_pct_across_funds DESC
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_fi_nac)
            SELECT
                c.rut_emisor,
                COALESCE(e.razon_social, c.rut_emisor)          AS nombre_emisor,
                COUNT(DISTINCT c.run_fondo)                     AS num_fondos,
                COUNT(DISTINCT fi.administrador)                AS num_admins,
                ROUND(AVG(c.pct_activo_fondo)::numeric, 4)      AS avg_pct_fondo,
                ROUND(SUM(c.pct_activo_fondo)::numeric, 2)      AS sum_pct_across_funds,
                STRING_AGG(DISTINCT c.tipo_instrumento, ', ')   AS tipos_instrumento
            FROM cartera_fi_nac c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            JOIN fondos_inversion fi ON fi.run_fondo = c.run_fondo
            WHERE c.periodo = (SELECT t FROM latest)
              AND c.rut_emisor IS NOT NULL
              {tipo_filter}
            GROUP BY c.rut_emisor, e.razon_social
            ORDER BY num_fondos DESC, sum_pct_across_funds DESC
            LIMIT :limit
        """, params)


@mcp.tool()
def emisor_fund_exposure(
    rut: str | None = None,
    nombre: str | None = None,
    fund_type: Literal["fm", "fi", "all"] = "all",
    limit: int = 50,
) -> dict:
    """
    Given a company (emisor) by RUT or partial name, show every fund that holds it
    and at what portfolio weight. Useful for systemic risk analysis or understanding
    how concentrated an issuer is across the Chilean fund industry.
    Domestic positions matched by rut_emisor (via SII); foreign positions matched
    by nombre_emisor partial name (only when nombre is provided, not rut).
    """
    if not rut and not nombre:
        return {"error": "Provide either rut or nombre"}

    result: dict = {}

    if fund_type in ("fm", "all"):
        # Domestic — match by RUT or SII name
        if rut:
            naci_condition = "c.rut_emisor = :id"
            params: dict = {"id": rut, "limit": limit}
        else:
            naci_condition = "e.razon_social ILIKE :id"
            params = {"id": f"%{nombre}%", "limit": limit}

        domestic = _rows(f"""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_naci)
            SELECT
                'naci'                                                           AS source,
                c.rut_emisor,
                COALESCE(e.razon_social, c.rut_emisor)                          AS nombre_emisor,
                c.run_fondo, fm.nombre_fondo,
                fm.razon_social_administradora                                   AS administrador,
                c.tipo_instrumento, c.nemotecnico,
                CAST(NULLIF(c.porcentaje_activos_fondo, '') AS numeric)          AS pct_activo_fondo,
                CAST(NULLIF(c.valorizacion_cierre, '')      AS numeric)          AS valorizacion_cierre,
                c.clasificacion_riesgo, c.tir, c.fecha_vencimiento
            FROM cartera_naci c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo
            WHERE c.periodo = (SELECT t FROM latest)
              AND {naci_condition}
            ORDER BY pct_activo_fondo DESC NULLS LAST
            LIMIT :limit
        """, params)

        # Foreign — match by nombre_emisor (only when searching by name)
        foreign = []
        if nombre:
            foreign = _rows("""
                WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_extr)
                SELECT
                    'extr'                                          AS source,
                    NULL                                            AS rut_emisor,
                    c.nombre_emisor,
                    c.run_fondo, fm.nombre_fondo,
                    fm.razon_social_administradora                  AS administrador,
                    c.tipo_instrumento, c.nemotecnico,
                    CAST(NULLIF(c.porcentaje_activos_fondo,'') AS numeric) AS pct_activo_fondo,
                    CAST(NULLIF(c.valorizacion_cierre,'')      AS numeric) AS valorizacion_cierre,
                    c.clasificacion_riesgo, c.tir, c.fecha_vencimiento
                FROM cartera_extr c
                JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo
                WHERE c.periodo = (SELECT t FROM latest)
                  AND c.nombre_emisor ILIKE :id
                ORDER BY pct_activo_fondo DESC NULLS LAST
                LIMIT :limit
            """, {"id": f"%{nombre}%", "limit": limit})

        result["fm"] = domestic + foreign

    if fund_type in ("fi", "all"):
        # Domestic — match by RUT or SII name
        if rut:
            naci_condition = "c.rut_emisor = :id"
            params = {"id": rut, "limit": limit}
        else:
            naci_condition = "e.razon_social ILIKE :id"
            params = {"id": f"%{nombre}%", "limit": limit}

        domestic = _rows(f"""
            WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_fi_nac)
            SELECT
                'naci'                                          AS source,
                c.rut_emisor,
                COALESCE(e.razon_social, c.rut_emisor)          AS nombre_emisor,
                c.run_fondo, fi.razon_social                    AS fondo,
                fi.administrador,
                c.tipo_instrumento, c.nemotecnico,
                c.pct_activo_fondo, c.valorizacion_cierre,
                c.clasif_riesgo, c.tir_val_par_precio, c.fecha_vencimiento
            FROM cartera_fi_nac c
            LEFT JOIN emisores e ON e.rut = c.rut_emisor
            JOIN fondos_inversion fi ON fi.run_fondo = c.run_fondo
            WHERE c.periodo = (SELECT t FROM latest)
              AND {naci_condition}
            ORDER BY c.pct_activo_fondo DESC NULLS LAST
            LIMIT :limit
        """, params)

        # Foreign — match by nombre_emisor (only when searching by name)
        foreign = []
        if nombre:
            foreign = _rows("""
                WITH latest AS (SELECT MAX(periodo) AS t FROM cartera_fi_ext)
                SELECT
                    'extr'                      AS source,
                    NULL                        AS rut_emisor,
                    c.nombre_emisor,
                    c.run_fondo, fi.razon_social AS fondo,
                    fi.administrador,
                    c.tipo_instrumento, c.nemo_isin AS nemotecnico,
                    c.pct_activo_fondo, c.valorizacion_cierre,
                    c.clasif_riesgo, c.tir_val_par_precio, c.fecha_vencimiento
                FROM cartera_fi_ext c
                JOIN fondos_inversion fi ON fi.run_fondo = c.run_fondo
                WHERE c.periodo = (SELECT t FROM latest)
                  AND c.nombre_emisor ILIKE :id
                ORDER BY c.pct_activo_fondo DESC NULLS LAST
                LIMIT :limit
            """, {"id": f"%{nombre}%", "limit": limit})

        result["fi"] = domestic + foreign

    return result


@mcp.tool()
def fi_equity_activity(
    activity_type: Literal["all", "raising", "returning", "pending_calls"] = "all",
    period: Literal["this_month", "last_month", "last_3m", "last_6m", "ytd", "last_12m"] = "last_3m",
    group_by: Literal["agf", "fund"] = "fund",
    admin: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """
    Track equity events for non-rescatable FI funds. Data is quarterly (cuotas_fi table).

    cuotas_emitidas and cuotas_pagadas are CUMULATIVE STOCKS (not period flows).
    The tool computes quarter-over-quarter deltas via LAG() to detect actual activity:

    activity_type:
      - 'raising': delta_pagadas > 0 (capital was called this quarter) OR
                   delta_emitidas > 0 (fund increased its authorized capital)
      - 'returning': delta_pagadas < 0 (capital was returned to investors)
      - 'pending_calls': cuotas_suscritas_no_pagadas > 0 (subscribed/signed but uncalled) OR
                         num_cuotas_promesa > 0 (promise-stage commitments not yet subscribed)
      - 'all': all funds with any activity, sorted by absolute capital movement

    Commitment pipeline stages (earliest → latest):
      promise → subscribed (suscritas_no_pagadas) → paid (pagadas)

    Key output fields:
      capital_called_bn_clp   = delta_pagadas × valor_libro (+ = called in, - = returned)
      new_auth_bn_clp         = delta_emitidas × valor_libro (authorization increase)
      pending_formal_bn_clp   = cuotas_suscritas_no_pagadas × valor_libro
      pending_promise_bn_clp  = num_cuotas_promesa × valor_libro
      num_contratos_promesa   = active promise contracts
      num_promitentes         = investors with open promise commitments

    group_by='fund': one row per fund — best for identifying specific funds.
    group_by='agf': aggregated by administrator — best for market-level view.
    from_date / to_date (YYYY-MM-DD): custom quarter range — overrides period.
    """
    params: dict = {"limit": limit}

    if from_date or to_date:
        conditions = ["valor_libro IS NOT NULL", "valor_libro > 0"]
        if from_date:
            conditions.append("periodo >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("periodo <= :to_date")
            params["to_date"] = to_date
        period_cond = " AND ".join(conditions)
    else:
        # Strip the cf. prefix — used inside CTE where alias is already resolved
        period_cond = (
            _FI_PERIOD_SQL[period]
            .replace("cf.periodo", "periodo")
            + " AND valor_libro IS NOT NULL AND valor_libro > 0"
        )

    activity_filter = _ACTIVITY_FILTER_SQL[activity_type]
    activity_having = _ACTIVITY_HAVING_SQL[activity_type]
    order_col       = _ACTIVITY_ORDER_COL[activity_type]
    admin_filter    = "AND fi.administrador ILIKE :admin" if admin else ""
    if admin:
        params["admin"] = f"%{admin}%"

    # LAG() runs over all history so deltas are correct even when only one
    # quarter falls inside the requested period window.
    if group_by == "fund":
        return _rows(f"""
            WITH history AS (
                SELECT
                    cf.run_fondo,
                    fi.razon_social                                                          AS nombre,
                    fi.administrador,
                    cf.periodo,
                    COALESCE(cf.cuotas_emitidas, 0)                                         AS cuotas_emitidas,
                    COALESCE(cf.cuotas_pagadas, 0)                                          AS cuotas_pagadas,
                    COALESCE(cf.cuotas_suscritas_no_pagadas, 0)                             AS cuotas_suscritas_no_pagadas,
                    COALESCE(cf.num_cuotas_promesa, 0)                                      AS num_cuotas_promesa,
                    cf.num_contratos_promesa,
                    cf.num_promitentes,
                    cf.valor_libro,
                    LAG(COALESCE(cf.cuotas_emitidas, 0)) OVER w                             AS prev_emitidas,
                    LAG(COALESCE(cf.cuotas_pagadas, 0))  OVER w                             AS prev_pagadas
                FROM cuotas_fi cf
                JOIN fondos_inversion fi ON fi.run_fondo = cf.run_fondo
                WHERE fi.rescatable = false
                  AND cf.valor_libro IS NOT NULL AND cf.valor_libro > 0
                  {admin_filter}
                WINDOW w AS (PARTITION BY cf.run_fondo ORDER BY cf.periodo)
            ),
            latest_in_period AS (
                SELECT DISTINCT ON (run_fondo) *,
                    cuotas_emitidas - COALESCE(prev_emitidas, cuotas_emitidas) AS delta_emitidas,
                    cuotas_pagadas  - COALESCE(prev_pagadas,  cuotas_pagadas)  AS delta_pagadas
                FROM history
                WHERE {period_cond}
                ORDER BY run_fondo, periodo DESC
            )
            SELECT
                run_fondo, nombre, administrador, periodo,
                cuotas_emitidas, prev_emitidas, delta_emitidas,
                cuotas_pagadas,  prev_pagadas,  delta_pagadas,
                cuotas_suscritas_no_pagadas,
                num_cuotas_promesa, num_contratos_promesa, num_promitentes,
                ROUND(delta_pagadas  * valor_libro / 1e9, 3)                              AS capital_called_bn_clp,
                ROUND(delta_emitidas * valor_libro / 1e9, 3)                              AS new_auth_bn_clp,
                ROUND(cuotas_suscritas_no_pagadas * valor_libro / 1e9, 3)                 AS pending_formal_bn_clp,
                ROUND(num_cuotas_promesa          * valor_libro / 1e9, 3)                 AS pending_promise_bn_clp
            FROM latest_in_period
            WHERE {activity_filter}
            ORDER BY {order_col} DESC NULLS LAST
            LIMIT :limit
        """, params)
    else:
        return _rows(f"""
            WITH history AS (
                SELECT
                    cf.run_fondo,
                    fi.administrador,
                    cf.periodo,
                    COALESCE(cf.cuotas_emitidas, 0)             AS cuotas_emitidas,
                    COALESCE(cf.cuotas_pagadas, 0)              AS cuotas_pagadas,
                    COALESCE(cf.cuotas_suscritas_no_pagadas, 0) AS cuotas_suscritas_no_pagadas,
                    COALESCE(cf.num_cuotas_promesa, 0)          AS num_cuotas_promesa,
                    cf.valor_libro,
                    LAG(COALESCE(cf.cuotas_emitidas, 0)) OVER w AS prev_emitidas,
                    LAG(COALESCE(cf.cuotas_pagadas, 0))  OVER w AS prev_pagadas
                FROM cuotas_fi cf
                JOIN fondos_inversion fi ON fi.run_fondo = cf.run_fondo
                WHERE fi.rescatable = false
                  AND cf.valor_libro IS NOT NULL AND cf.valor_libro > 0
                  {admin_filter}
                WINDOW w AS (PARTITION BY cf.run_fondo ORDER BY cf.periodo)
            ),
            latest_in_period AS (
                SELECT DISTINCT ON (run_fondo) *,
                    cuotas_emitidas - COALESCE(prev_emitidas, cuotas_emitidas) AS delta_emitidas,
                    cuotas_pagadas  - COALESCE(prev_pagadas,  cuotas_pagadas)  AS delta_pagadas
                FROM history
                WHERE {period_cond}
                ORDER BY run_fondo, periodo DESC
            )
            SELECT
                administrador,
                COUNT(*) FILTER (WHERE delta_pagadas > 0 OR delta_emitidas > 0)         AS fondos_raising,
                COUNT(*) FILTER (WHERE delta_pagadas < 0)                                AS fondos_returning,
                COUNT(*) FILTER (WHERE cuotas_suscritas_no_pagadas > 0
                                    OR num_cuotas_promesa > 0)                           AS fondos_pending_calls,
                COUNT(*)                                                                  AS total_fondos,
                ROUND(SUM(delta_pagadas  * valor_libro) / 1e9, 2)                        AS capital_called_bn_clp,
                ROUND(SUM(delta_emitidas * valor_libro) / 1e9, 2)                        AS new_auth_bn_clp,
                ROUND(SUM(cuotas_suscritas_no_pagadas * valor_libro) / 1e9, 2)            AS pending_formal_bn_clp,
                ROUND(SUM(num_cuotas_promesa          * valor_libro) / 1e9, 2)            AS pending_promise_bn_clp
            FROM latest_in_period
            GROUP BY administrador
            HAVING {activity_having}
            ORDER BY {order_col} DESC NULLS LAST
            LIMIT :limit
        """, params)


@mcp.tool()
def portfolio_overlap(
    run_fondo_a: str,
    run_fondo_b: str,
    fund_type: Literal["fm", "fi"] = "fm",
) -> dict:
    """
    Portfolio overlap between two funds for the latest quarter.
    Returns shared positions (matched by rut_emisor), exclusive positions, and an overlap score.
    Useful for detecting closet indexing, comparing similar strategies, or M&A synergy analysis.
    Use search_funds first to find run_fondo values.
    """
    if fund_type == "fm":
        shared = _rows("""
            WITH la AS (SELECT MAX(periodo) AS t FROM cartera_naci WHERE run_fondo = :a),
                 lb AS (SELECT MAX(periodo) AS t FROM cartera_naci WHERE run_fondo = :b)
            SELECT
                a.rut_emisor,
                COALESCE(e.razon_social, a.rut_emisor)                         AS nombre_emisor,
                a.nemotecnico, a.tipo_instrumento,
                CAST(NULLIF(a.porcentaje_activos_fondo, '') AS numeric)         AS pct_fondo_a,
                CAST(NULLIF(b.porcentaje_activos_fondo, '') AS numeric)         AS pct_fondo_b
            FROM cartera_naci a
            JOIN cartera_naci b
              ON b.rut_emisor = a.rut_emisor
             AND b.run_fondo = :b AND b.periodo = (SELECT t FROM lb)
            LEFT JOIN emisores e ON e.rut = a.rut_emisor
            WHERE a.run_fondo = :a AND a.periodo = (SELECT t FROM la)
              AND a.rut_emisor IS NOT NULL
            ORDER BY pct_fondo_a DESC NULLS LAST
        """, {"a": run_fondo_a, "b": run_fondo_b})

        count_a = _scalar("""
            SELECT COUNT(*) FROM cartera_naci
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run)
        """, {"run": run_fondo_a}) or 0
        count_b = _scalar("""
            SELECT COUNT(*) FROM cartera_naci
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_naci WHERE run_fondo = :run)
        """, {"run": run_fondo_b}) or 0
    else:
        shared = _rows("""
            WITH la AS (SELECT MAX(periodo) AS t FROM cartera_fi_nac WHERE run_fondo = :a),
                 lb AS (SELECT MAX(periodo) AS t FROM cartera_fi_nac WHERE run_fondo = :b)
            SELECT
                a.rut_emisor,
                COALESCE(e.razon_social, a.rut_emisor)  AS nombre_emisor,
                a.nemotecnico, a.tipo_instrumento,
                a.pct_activo_fondo                       AS pct_fondo_a,
                b.pct_activo_fondo                       AS pct_fondo_b
            FROM cartera_fi_nac a
            JOIN cartera_fi_nac b
              ON b.rut_emisor = a.rut_emisor
             AND b.run_fondo = :b AND b.periodo = (SELECT t FROM lb)
            LEFT JOIN emisores e ON e.rut = a.rut_emisor
            WHERE a.run_fondo = :a AND a.periodo = (SELECT t FROM la)
              AND a.rut_emisor IS NOT NULL
            ORDER BY a.pct_activo_fondo DESC NULLS LAST
        """, {"a": run_fondo_a, "b": run_fondo_b})

        count_a = _scalar("""
            SELECT COUNT(*) FROM cartera_fi_nac
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run)
        """, {"run": run_fondo_a}) or 0
        count_b = _scalar("""
            SELECT COUNT(*) FROM cartera_fi_nac
            WHERE run_fondo = :run
              AND periodo = (SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run)
        """, {"run": run_fondo_b}) or 0

    n_shared = len(shared)
    union = count_a + count_b - n_shared
    jaccard = round(n_shared / union * 100, 1) if union else 0.0

    return {
        "summary": {
            "positions_fund_a": count_a,
            "positions_fund_b": count_b,
            "shared_positions": n_shared,
            "jaccard_overlap_pct": jaccard,
        },
        "shared_positions": shared,
    }
