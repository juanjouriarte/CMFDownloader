from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/industry", tags=["industry"])

FundType = Literal["all", "fm", "fi"]
GroupBy = Literal["market", "admin", "category"]

_SORT_COLUMNS = {
    "aum": "aum_clp",
    "r_1y": "r_1y",
    "r_ytd": "r_ytd",
    "nnm": "nnm_ytd_clp",
}

_SNAPSHOT_CTE = """
WITH fm_ref AS (
    SELECT fecha FROM cartola_diaria
    WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
    GROUP BY fecha
    ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
    LIMIT 1
),
fm_nav AS (
    SELECT cd.run_fondo, cd.serie, cd.fecha, cd.patrimonio_neto
    FROM cartola_diaria cd
    WHERE cd.fecha = (SELECT fecha FROM fm_ref)
),
fm_aum AS (
    SELECT run_fondo, SUM(patrimonio_neto) AS aum_clp, MAX(fecha) AS data_date
    FROM fm_nav GROUP BY run_fondo
),
fm_series AS (
    SELECT DISTINCT ON (run_fondo) run_fondo, serie
    FROM fm_nav
    ORDER BY run_fondo, patrimonio_neto DESC NULLS LAST, serie
),
fm_cat AS (
    SELECT DISTINCT ON (run_fondo) run_fondo, tipo, grupo, categoria, nombre_cat
    FROM categoria_fm
    ORDER BY run_fondo, periodo DESC
),
fm_flows AS (
    SELECT run_fondo,
           SUM(monto_aportado - monto_rescatado)
               FILTER (WHERE fecha >= DATE_TRUNC('month', CURRENT_DATE)) AS nnm_month_clp,
           SUM(monto_aportado - monto_rescatado)
               FILTER (WHERE fecha >= DATE_TRUNC('year', CURRENT_DATE)) AS nnm_ytd_clp
    FROM cartola_diaria
    WHERE fecha >= DATE_TRUNC('year', CURRENT_DATE)
    GROUP BY run_fondo
),
fm AS (
    SELECT 'fm'::text AS fund_type, f.run_fondo, f.nombre_fondo AS name,
           f.razon_social_administradora AS administrator,
           f.fecha_termino_operaciones IS NULL AS vigente,
           NULL::boolean AS rescatable,
           c.tipo AS category_type, c.grupo AS category_group,
           c.categoria AS category, c.nombre_cat AS category_name,
           a.aum_clp,
           NULL::numeric AS aum_usd,
           NULL::numeric AS aum_eur,
           NULL::numeric AS aum_other,
           a.data_date, r.r_1y, r.r_ytd,
           COALESCE(fl.nnm_month_clp, 0) AS nnm_month_clp,
           COALESCE(fl.nnm_ytd_clp, 0) AS nnm_ytd_clp
    FROM fondo_mutuo f
    LEFT JOIN fm_aum a ON a.run_fondo = f.run_fondo
    LEFT JOIN fm_series s ON s.run_fondo = f.run_fondo
    LEFT JOIN v_rentabilidad_fm_quality r
      ON r.run_fondo = s.run_fondo AND r.serie IS NOT DISTINCT FROM s.serie
     AND NOT r.is_data_suspicious
    LEFT JOIN fm_cat c ON c.run_fondo = f.run_fondo
    LEFT JOIN fm_flows fl ON fl.run_fondo = f.run_fondo
),
fi_ref AS (
    SELECT fecha FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    WHERE f.vigente = true
      AND v.fecha >= (SELECT MAX(fecha) FROM valores_cuota_fi) - 7
    GROUP BY fecha
    ORDER BY COUNT(DISTINCT v.run_fondo) DESC, fecha DESC
    LIMIT 1
),
fi_nav AS (
    SELECT v.run_fondo, v.serie, v.fecha, v.patrimonio_neto, v.moneda
    FROM valores_cuota_fi v
    WHERE v.fecha = (SELECT fecha FROM fi_ref)
),
fi_aum AS (
    SELECT run_fondo,
           SUM(CASE WHEN moneda IN ('$$') OR moneda IS NULL THEN patrimonio_neto ELSE 0 END) AS aum_clp,
           SUM(CASE WHEN moneda = 'PROM' THEN patrimonio_neto ELSE 0 END) AS aum_usd,
           SUM(CASE WHEN moneda = 'EUR'  THEN patrimonio_neto ELSE 0 END) AS aum_eur,
           SUM(CASE WHEN moneda NOT IN ('$$', 'PROM', 'EUR', '0') AND moneda IS NOT NULL
                    THEN patrimonio_neto ELSE 0 END)                       AS aum_other,
           MAX(fecha) AS data_date
    FROM fi_nav GROUP BY run_fondo
),
fi_series AS (
    SELECT DISTINCT ON (run_fondo) run_fondo, serie
    FROM fi_nav
    ORDER BY run_fondo, patrimonio_neto DESC NULLS LAST, serie
),
fi_cat AS (
    SELECT DISTINCT ON (run_fondo) run_fondo, tipo, grupo, categoria, nombre_cat
    FROM categoria_fi
    ORDER BY run_fondo, periodo DESC
),
fi_flows_rescatable AS (
    SELECT run_fondo,
           SUM(flujo_neto) FILTER (WHERE fecha >= DATE_TRUNC('month', CURRENT_DATE)) AS nnm_month_clp,
           SUM(flujo_neto) FILTER (WHERE fecha >= DATE_TRUNC('year', CURRENT_DATE)) AS nnm_ytd_clp
    FROM valores_cuota_fi
    WHERE fecha >= DATE_TRUNC('year', CURRENT_DATE)
    GROUP BY run_fondo
),
fi_flows_non_rescatable AS (
    SELECT cf.run_fondo,
           SUM((cf.cuotas_emitidas - cf.cuotas_pagadas) * cf.valor_libro)
               FILTER (WHERE cf.periodo >= DATE_TRUNC('month', CURRENT_DATE)) AS nnm_month_clp,
           SUM((cf.cuotas_emitidas - cf.cuotas_pagadas) * cf.valor_libro)
               FILTER (WHERE cf.periodo >= DATE_TRUNC('year', CURRENT_DATE)) AS nnm_ytd_clp
    FROM cuotas_fi cf
    JOIN fondos_inversion f ON f.run_fondo = cf.run_fondo
    WHERE f.rescatable = false
      AND cf.periodo >= DATE_TRUNC('year', CURRENT_DATE)
      AND cf.valor_libro > 0
    GROUP BY cf.run_fondo
),
fi_flows AS (
    SELECT COALESCE(r.run_fondo, n.run_fondo) AS run_fondo,
           COALESCE(r.nnm_month_clp, n.nnm_month_clp, 0) AS nnm_month_clp,
           COALESCE(r.nnm_ytd_clp, n.nnm_ytd_clp, 0) AS nnm_ytd_clp
    FROM fi_flows_rescatable r
    FULL OUTER JOIN fi_flows_non_rescatable n ON n.run_fondo = r.run_fondo
),
fi AS (
    SELECT 'fi'::text AS fund_type, f.run_fondo, f.razon_social AS name,
           f.administrador AS administrator, COALESCE(f.vigente, false) AS vigente,
           f.rescatable, c.tipo AS category_type, c.grupo AS category_group,
           c.categoria AS category, c.nombre_cat AS category_name,
           NULLIF(a.aum_clp, 0) AS aum_clp,
           NULLIF(a.aum_usd, 0) AS aum_usd,
           NULLIF(a.aum_eur, 0) AS aum_eur,
           NULLIF(a.aum_other, 0) AS aum_other,
           a.data_date, r.r_1y, r.r_ytd,
           COALESCE(fl.nnm_month_clp, 0) AS nnm_month_clp,
           COALESCE(fl.nnm_ytd_clp, 0) AS nnm_ytd_clp
    FROM fondos_inversion f
    LEFT JOIN fi_aum a ON a.run_fondo = f.run_fondo
    LEFT JOIN fi_series s ON s.run_fondo = f.run_fondo
    LEFT JOIN mv_rentabilidad_fi r
      ON r.run_fondo = s.run_fondo AND r.serie IS NOT DISTINCT FROM s.serie
    LEFT JOIN fi_cat c ON c.run_fondo = f.run_fondo
    LEFT JOIN fi_flows fl ON fl.run_fondo = f.run_fondo
),
snapshot AS (
    SELECT * FROM fm
    UNION ALL
    SELECT * FROM fi
)
"""


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with SessionLocal() as session:
        result = session.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in result]


@router.get("/funds")
def industry_funds(
    pagination: Pagination,
    _: CacheHook,
    fund_type: FundType = "all",
    type: str | None = Query(None),
    group: str | None = Query(None),
    category: str | None = Query(None),
    admin: str | None = Query(None),
    rescatable: bool | None = Query(None),
    vigente: bool | None = Query(True),
    sort: Literal["aum", "r_1y", "r_ytd", "nnm"] = "aum",
) -> list[dict]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if fund_type != "all":
        conditions.append("fund_type = :fund_type")
        params["fund_type"] = fund_type
    if type:
        conditions.append("category_type = :type")
        params["type"] = type
    if group:
        conditions.append("category_group = :group")
        params["group"] = group
    if category:
        conditions.append("category = :category")
        params["category"] = category
    if admin:
        conditions.append("administrator ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if rescatable is not None:
        conditions.append("rescatable = :rescatable")
        params["rescatable"] = rescatable
    if vigente is not None:
        conditions.append("vigente = :vigente")
        params["vigente"] = vigente
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sort_column = _SORT_COLUMNS[sort]
    return _rows(f"""
        {_SNAPSHOT_CTE}
        SELECT * FROM snapshot
        {where}
        ORDER BY {sort_column} DESC NULLS LAST, name
        LIMIT :limit OFFSET :offset
    """, params)


@router.get("/overview")
def industry_overview(
    _: CacheHook,
    fund_type: FundType = "all",
    categoria: str | None = Query(None),
    tipo: str | None = Query(None),
) -> dict:
    conditions: list[str] = []
    params: dict = {}
    if fund_type != "all":
        conditions.append("fund_type = :fund_type")
        params["fund_type"] = fund_type
    if categoria:
        conditions.append("category = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("category_type = :tipo")
        params["tipo"] = tipo
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # FM gross flows for aportes/rescates breakdown
    fm_flow_conditions: list[str] = []
    if fund_type == "fi":
        fm_flow_conditions.append("1=0")  # exclude FM entirely
    fi_flow_conditions: list[str] = []
    if fund_type == "fm":
        fi_flow_conditions.append("1=0")  # exclude FI entirely

    summary = _rows(f"""
        {_SNAPSHOT_CTE}
        SELECT SUM(aum_clp)   AS total_aum_clp,
               SUM(aum_usd)   AS total_aum_usd,
               SUM(aum_eur)   AS total_aum_eur,
               SUM(aum_other) AS total_aum_other,
               COUNT(*) FILTER (WHERE vigente) AS active_funds,
               COUNT(DISTINCT administrator) FILTER (WHERE vigente) AS administrators,
               MAX(data_date) AS latest_data_date,
               SUM(nnm_month_clp) AS net_flow_month_clp,
               SUM(nnm_ytd_clp) AS net_flow_ytd_clp
        FROM snapshot {where}
    """, params)[0]

    # Gross aportes + rescates (FM only — FI doesn't have granular aportes/rescates)
    fm_gross_conds = ["fecha >= DATE_TRUNC('month', CURRENT_DATE)"]
    if categoria or tipo:
        fm_gross_conds.append("""run_fondo IN (
            SELECT DISTINCT ON (run_fondo) run_fondo FROM categoria_fm
            WHERE 1=1
            {cat_filter}
            ORDER BY run_fondo, periodo DESC
        )""".format(cat_filter=(
            f"AND categoria = :categoria" if categoria else ""
        ) + (
            f" AND tipo = :tipo" if tipo else ""
        )))
    if fund_type != "fi":
        gross_flows = _rows(f"""
            SELECT SUM(monto_aportado) AS aportes_month_clp,
                   SUM(monto_rescatado) AS rescates_month_clp,
                   SUM(monto_aportado - monto_rescatado) AS neto_month_clp
            FROM cartola_diaria
            WHERE {" AND ".join(fm_gross_conds)}
        """, params)[0]
    else:
        gross_flows = {"aportes_month_clp": None, "rescates_month_clp": None, "neto_month_clp": None}

    breakdown = _rows(f"""
        {_SNAPSHOT_CTE}
        SELECT fund_type, SUM(aum_clp) AS aum_clp,
               COUNT(*) FILTER (WHERE vigente) AS active_funds,
               MAX(data_date) AS latest_data_date,
               SUM(nnm_month_clp) AS net_flow_month_clp,
               SUM(nnm_ytd_clp) AS net_flow_ytd_clp
        FROM snapshot {where}
        GROUP BY fund_type ORDER BY fund_type
    """, params)
    top_admins = _rows(f"""
        {_SNAPSHOT_CTE}
        SELECT administrator, SUM(aum_clp) AS aum_clp,
               ROUND(SUM(aum_clp) * 100.0 / NULLIF(SUM(SUM(aum_clp)) OVER (), 0), 2)
                   AS market_share_pct,
               COUNT(*) FILTER (WHERE vigente) AS active_funds,
               SUM(nnm_ytd_clp) AS nnm_ytd_clp
        FROM snapshot {where}
        GROUP BY administrator
        ORDER BY aum_clp DESC NULLS LAST LIMIT 10
    """, params)
    categories = _rows(f"""
        {_SNAPSHOT_CTE}
        SELECT fund_type, category_type AS type, category_group AS "group",
               category, category_name, SUM(aum_clp) AS aum_clp,
               COUNT(*) FILTER (WHERE vigente) AS active_funds,
               SUM(nnm_ytd_clp) AS nnm_ytd_clp
        FROM snapshot {where}
        GROUP BY fund_type, category_type, category_group, category, category_name
        ORDER BY aum_clp DESC NULLS LAST
    """, params)
    return {
        **summary,
        **gross_flows,
        "breakdown": breakdown,
        "top_administrators": top_admins,
        "category_aum_breakdown": categories,
    }


@router.get("/evolution")
def industry_evolution(
    _: CacheHook,
    fund_type: FundType = "all",
    group_by: GroupBy = "market",
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    categoria: str | None = Query(None),
    tipo: str | None = Query(None),
) -> list[dict]:
    group_expr = {
        "market": "fund_type",
        "admin": "administrator",
        "category": "category",
    }[group_by]
    conditions = [
        "data_date >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')",
        "data_date <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)",
    ]
    params: dict = {"from_date": from_date, "to_date": to_date}
    if fund_type != "all":
        conditions.append("fund_type = :fund_type")
        params["fund_type"] = fund_type
    if categoria:
        conditions.append("category = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("category_tipo = :tipo")
        params["tipo"] = tipo
    where = " AND ".join(conditions)
    return _rows(f"""
        WITH fm_daily AS (
            SELECT cd.fecha AS data_date, 'fm'::text AS fund_type, cd.run_fondo,
                   fm.razon_social_administradora AS administrator,
                   SUM(cd.patrimonio_neto) AS aum_clp,
                   SUM(cd.monto_aportado) AS aportes_clp,
                   SUM(cd.monto_rescatado) AS rescates_clp
            FROM cartola_diaria cd
            JOIN fondo_mutuo fm ON fm.run_fondo = cd.run_fondo
            WHERE cd.fecha >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
              AND cd.fecha <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
            GROUP BY cd.fecha, cd.run_fondo, fm.razon_social_administradora
        ),
        fm_monthly AS (
            SELECT DISTINCT ON (run_fondo, DATE_TRUNC('month', data_date))
                   data_date, fund_type, run_fondo, administrator, aum_clp,
                   aportes_clp, rescates_clp
            FROM fm_daily
            ORDER BY run_fondo, DATE_TRUNC('month', data_date), data_date DESC
        ),
        fi_daily AS (
            SELECT v.fecha AS data_date, 'fi'::text AS fund_type, v.run_fondo,
                   fi.administrador AS administrator, SUM(v.patrimonio_neto) AS aum_clp,
                   NULL::numeric AS aportes_clp, NULL::numeric AS rescates_clp
            FROM valores_cuota_fi v
            JOIN fondos_inversion fi ON fi.run_fondo = v.run_fondo
            WHERE v.fecha >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
              AND v.fecha <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
            GROUP BY v.fecha, v.run_fondo, fi.administrador
        ),
        fi_monthly AS (
            SELECT DISTINCT ON (run_fondo, DATE_TRUNC('month', data_date))
                   data_date, fund_type, run_fondo, administrator, aum_clp,
                   aportes_clp, rescates_clp
            FROM fi_daily
            ORDER BY run_fondo, DATE_TRUNC('month', data_date), data_date DESC
        ),
        fm_cat AS (
            SELECT DISTINCT ON (run_fondo) run_fondo, categoria AS category, tipo AS category_tipo
            FROM categoria_fm ORDER BY run_fondo, periodo DESC
        ),
        fi_cat AS (
            SELECT DISTINCT ON (run_fondo) run_fondo, categoria AS category, tipo AS category_tipo
            FROM categoria_fi ORDER BY run_fondo, periodo DESC
        ),
        history AS (
            SELECT x.*, c.category, c.category_tipo
            FROM fm_monthly x LEFT JOIN fm_cat c USING (run_fondo)
            UNION ALL
            SELECT x.*, c.category, c.category_tipo
            FROM fi_monthly x LEFT JOIN fi_cat c USING (run_fondo)
        ),
        grouped AS (
            SELECT DATE_TRUNC('month', data_date)::date AS date,
                   fund_type, COALESCE({group_expr}, 'Sin clasificar') AS group_key,
                   SUM(aum_clp) AS aum_clp,
                   SUM(aportes_clp) AS aportes_clp,
                   SUM(rescates_clp) AS rescates_clp,
                   SUM(aportes_clp - rescates_clp) AS nnm_clp
            FROM history
            WHERE {where}
            GROUP BY DATE_TRUNC('month', data_date)::date, fund_type,
                     COALESCE({group_expr}, 'Sin clasificar')
        )
        SELECT date, fund_type, group_key, aum_clp,
               aportes_clp, rescates_clp, nnm_clp,
               ROUND(aum_clp * 100.0 / NULLIF(SUM(aum_clp) OVER (PARTITION BY date), 0), 2)
                   AS market_share_pct
        FROM grouped
        ORDER BY date, aum_clp DESC NULLS LAST
    """, params)
