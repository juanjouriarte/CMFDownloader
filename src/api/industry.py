from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/industry", tags=["industry"])
FundType = Literal["all", "fm", "fi"]
GroupBy = Literal["market", "admin", "category"]
Currency = Annotated[str, Query(pattern=r"^(?:[A-Z]{3}|UF)$", description="Reporting currency; no FX conversion")]


def _currency(column: str) -> str:
    return f"""CASE UPPER(TRIM({column}))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM({column})) END"""


FM_CURRENCY = f"COALESCE({_currency('cd.moneda')}, {_currency('f.moneda')})"
FI_CURRENCY = f"COALESCE({_currency('v.moneda')}, {_currency('f.moneda')})"


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with SessionLocal() as session:
        result = session.execute(text(sql), params or {}).mappings().all()
    return [dict(row) for row in result]


def _reference(table: str) -> str:
    # Prefer the newest date with near-complete coverage, not the fullest older date.
    return f"""SELECT MAX(fecha) FROM (
        SELECT fecha, COUNT(DISTINCT run_fondo) AS coverage,
               MAX(COUNT(DISTINCT run_fondo)) OVER () AS max_coverage
        FROM {table}
        WHERE fecha >= (SELECT MAX(fecha) FROM {table}) - 7
        GROUP BY fecha
    ) dates WHERE coverage >= max_coverage * 0.9"""


def _snapshot_cte(include_returns: bool = True, include_ytd: bool = True) -> str:
    flow_period = "year" if include_ytd else "month"
    # Scalar date bounds become InitPlans, allowing indexed range scans instead
    # of joining the reference row against the entire daily-history tables.
    return f"""WITH
    fm_ref AS (SELECT ({_reference('cartola_diaria')}) AS fecha),
    fi_ref AS (SELECT ({_reference('valores_cuota_fi')}) AS fecha),
    fm_nav AS (
        SELECT cd.*, {FM_CURRENCY} AS currency
        FROM cartola_diaria cd JOIN fondo_mutuo f USING (run_fondo)
        WHERE :fund_type != 'fi' AND cd.fecha = (SELECT fecha FROM fm_ref)
    ),
    fi_nav AS (
        SELECT v.*, {FI_CURRENCY} AS currency
        FROM valores_cuota_fi v JOIN fondos_inversion f USING (run_fondo)
        WHERE :fund_type != 'fm' AND v.fecha = (SELECT fecha FROM fi_ref)
    ),
    fm_aum AS (
        SELECT run_fondo, currency, SUM(patrimonio_neto) AS aum, MAX(fecha) AS data_date
        FROM fm_nav GROUP BY run_fondo, currency
    ),
    fi_aum AS (
        SELECT run_fondo, currency, SUM(patrimonio_neto) AS aum, MAX(fecha) AS data_date
        FROM fi_nav GROUP BY run_fondo, currency
    ),
    fm_series AS (
        SELECT DISTINCT ON (run_fondo, currency) run_fondo, currency, serie
        FROM fm_nav ORDER BY run_fondo, currency, patrimonio_neto DESC NULLS LAST, serie
    ),
    fi_series AS (
        SELECT DISTINCT ON (run_fondo, currency) run_fondo, currency, serie
        FROM fi_nav ORDER BY run_fondo, currency, patrimonio_neto DESC NULLS LAST, serie
    ),
    fm_cat AS (
        SELECT DISTINCT ON (run_fondo) * FROM categoria_fm_effective ORDER BY run_fondo, periodo DESC
    ),
    fi_cat AS (
        SELECT DISTINCT ON (run_fondo) * FROM categoria_fi_effective ORDER BY run_fondo, periodo DESC
    ),
    fm_reported_raw AS (
        SELECT cd.run_fondo, {FM_CURRENCY} AS currency,
               SUM(cd.monto_aportado) FILTER (WHERE cd.fecha >= DATE_TRUNC('month', ref.fecha)) AS aportes_month,
               SUM(cd.monto_rescatado) FILTER (WHERE cd.fecha >= DATE_TRUNC('month', ref.fecha)) AS rescates_month,
               SUM(cd.monto_aportado - cd.monto_rescatado)
                   FILTER (WHERE cd.fecha >= DATE_TRUNC('month', ref.fecha)) AS nnm_month,
               {'SUM(cd.monto_aportado - cd.monto_rescatado)' if include_ytd else 'NULL::numeric'} AS nnm_ytd
        FROM cartola_diaria cd JOIN fondo_mutuo f USING (run_fondo) CROSS JOIN fm_ref ref
        WHERE :fund_type != 'fi'
          AND cd.fecha >= (SELECT DATE_TRUNC('{flow_period}', fecha) FROM fm_ref)
          AND cd.fecha <= (SELECT fecha FROM fm_ref)
          AND (:currency IS NULL OR {FM_CURRENCY} = :currency)
        GROUP BY cd.run_fondo, cd.moneda, f.moneda
    ),
    fm_reported AS (
        SELECT run_fondo, currency, SUM(aportes_month) AS aportes_month,
               SUM(rescates_month) AS rescates_month, SUM(nnm_month) AS nnm_month, SUM(nnm_ytd) AS nnm_ytd
        FROM fm_reported_raw GROUP BY run_fondo, currency
    ),
    adjustments AS (
        SELECT target_run_fondo AS run_fondo, currency,
               SUM(amount) FILTER (WHERE event_date >= DATE_TRUNC('month', ref.fecha)) AS month_amount,
               {'SUM(amount)' if include_ytd else 'NULL::numeric'} AS ytd_amount
        FROM fm_flow_adjustments CROSS JOIN fm_ref ref
        WHERE :fund_type != 'fi' AND status IN ('auto_confirmed', 'confirmed')
          AND event_date >= DATE_TRUNC('{flow_period}', ref.fecha) AND event_date <= ref.fecha
          AND (:currency IS NULL OR currency = :currency)
        GROUP BY target_run_fondo, currency
    ),
    fi_flows AS (
        SELECT v.run_fondo, {FI_CURRENCY} AS currency,
               SUM(v.flujo_neto) FILTER (WHERE v.fecha >= DATE_TRUNC('month', ref.fecha)) AS nnm_month,
               {'SUM(v.flujo_neto)' if include_ytd else 'NULL::numeric'} AS nnm_ytd
        FROM valores_cuota_fi v JOIN fondos_inversion f USING (run_fondo) CROSS JOIN fi_ref ref
        WHERE :fund_type != 'fm' AND f.rescatable = true
          AND v.fecha >= (SELECT DATE_TRUNC('{flow_period}', fecha) FROM fi_ref)
          AND v.fecha <= (SELECT fecha FROM fi_ref)
          AND (:currency IS NULL OR {FI_CURRENCY} = :currency)
        GROUP BY v.run_fondo, {FI_CURRENCY}
    ),
    snapshot AS (
        SELECT 'fm'::text AS fund_type, f.run_fondo, f.nombre_fondo AS name,
               f.razon_social_administradora AS administrator,
               f.fecha_termino_operaciones IS NULL AS vigente, NULL::boolean AS rescatable,
               c.tipo AS category_type, c.grupo AS category_group, c.categoria AS category,
               c.nombre_cat AS category_name, COALESCE(a.currency, {_currency('f.moneda')}) AS currency, a.aum, a.data_date, {'r.r_1y, r.r_ytd' if include_returns else 'NULL::numeric AS r_1y, NULL::numeric AS r_ytd'},
               fl.aportes_month, fl.rescates_month,
               fl.nnm_month - COALESCE(adj.month_amount, 0) AS nnm_month,
               fl.nnm_ytd - COALESCE(adj.ytd_amount, 0) AS nnm_ytd
        FROM fondo_mutuo f LEFT JOIN fm_aum a USING (run_fondo)
        LEFT JOIN fm_series s USING (run_fondo, currency)
        {'LEFT JOIN v_rentabilidad_fm_quality r ON r.run_fondo = s.run_fondo AND r.serie IS NOT DISTINCT FROM s.serie AND NOT r.is_data_suspicious' if include_returns else ''}
        LEFT JOIN fm_cat c ON c.run_fondo = f.run_fondo
        LEFT JOIN fm_reported fl ON fl.run_fondo = a.run_fondo AND fl.currency = a.currency
        LEFT JOIN adjustments adj ON adj.run_fondo = a.run_fondo AND adj.currency = a.currency
        WHERE :fund_type != 'fi'
        UNION ALL
        SELECT 'fi', f.run_fondo, f.razon_social, f.administrador,
               COALESCE(f.vigente, false), f.rescatable,
               c.tipo, c.grupo, c.categoria, c.nombre_cat,
               COALESCE(a.currency, {_currency('f.moneda')}), a.aum, a.data_date, {'r.r_1y, r.r_ytd' if include_returns else 'NULL::numeric AS r_1y, NULL::numeric AS r_ytd'},
               NULL::numeric, NULL::numeric, fl.nnm_month, fl.nnm_ytd
        FROM fondos_inversion f LEFT JOIN fi_aum a USING (run_fondo)
        LEFT JOIN fi_series s USING (run_fondo, currency)
        {'LEFT JOIN mv_rentabilidad_fi r ON r.run_fondo = s.run_fondo AND r.serie IS NOT DISTINCT FROM s.serie' if include_returns else ''}
        LEFT JOIN fi_cat c ON c.run_fondo = f.run_fondo
        LEFT JOIN fi_flows fl ON fl.run_fondo = a.run_fondo AND fl.currency = a.currency
        WHERE :fund_type != 'fm'
    )"""


def _filters(params: dict) -> str:
    clauses = []
    for key, column in [('type', 'category_type'), ('group', 'category_group'),
                        ('category', 'category'), ('nombre_cat', 'category_name'),
                        ('rescatable', 'rescatable'), ('vigente', 'vigente'), ('currency', 'currency')]:
        if params.get(key) is not None:
            clauses.append(f'{column} = :{key}')
    if params.get('admin'):
        clauses.append('administrator ILIKE :admin')
    return 'WHERE ' + ' AND '.join(clauses) if clauses else ''


def _legacy(row: dict, currency: str | None) -> dict:
    # Existing consumers may still read *_clp. Never put another currency in those fields.
    for key in ('aum', 'total_aum', 'aportes_month', 'rescates_month', 'neto_month',
                'nnm_month', 'nnm_ytd', 'net_flow_month', 'net_flow_ytd', 'aportes', 'rescates', 'nnm'):
        if key in row:
            row[key + '_clp'] = row[key] if currency == 'CLP' else None
    return row


@router.get('/currencies')
def industry_currencies(_: CacheHook) -> list[str]:
    rows = _rows(f"""SELECT DISTINCT currency FROM (
        SELECT {FM_CURRENCY} AS currency FROM cartola_diaria cd JOIN fondo_mutuo f USING (run_fondo)
        WHERE cd.fecha = (SELECT MAX(fecha) FROM cartola_diaria)
        UNION
        SELECT {FI_CURRENCY} AS currency FROM valores_cuota_fi v JOIN fondos_inversion f USING (run_fondo)
        WHERE v.fecha = (SELECT MAX(fecha) FROM valores_cuota_fi)
    ) currencies WHERE currency ~ '^(?:[A-Z]{{3}}|UF)$' ORDER BY currency""")
    return [r['currency'] for r in rows]


@router.get('/funds')
def industry_funds(
    pagination: Pagination, _: CacheHook,
    fund_type: FundType = 'all', currency: Currency | None = None,
    type: str | None = None, group: str | None = None, category: str | None = None,
    admin: str | None = None, rescatable: bool | None = None, vigente: bool | None = True,
    sort: Literal['aum', 'r_1y', 'r_ytd', 'nnm'] = 'aum',
) -> list[dict]:
    limit, offset = pagination
    params = dict(fund_type=fund_type, currency=currency, type=type, group=group,
                  category=category, admin=f'%{admin}%' if admin else None,
                  rescatable=rescatable, vigente=vigente, limit=limit, offset=offset)
    sort_column = {'aum': 'aum', 'nnm': 'nnm_ytd', 'r_1y': 'r_1y', 'r_ytd': 'r_ytd'}[sort]
    rows = _rows(f"""{_snapshot_cte()}
        SELECT * FROM snapshot {_filters(params)}
        ORDER BY currency, {sort_column} DESC NULLS LAST, name, run_fondo
        LIMIT :limit OFFSET :offset""", params)
    for r in rows:
        _legacy(r, r['currency'])
        r['aum_usd'] = r['aum'] if r['currency'] == 'USD' else None
        r['aum_eur'] = r['aum'] if r['currency'] == 'EUR' else None
    return rows


def _sum(rows: list[dict], key: str):
    values = [r[key] for r in rows if r.get(key) is not None]
    return sum(values, Decimal(0)) if values else None


def _aggregate(rows: list[dict], currency: str) -> dict:
    return _legacy(dict(
        aum=_sum(rows, 'aum'), active_funds=len({(r['fund_type'], r['run_fondo']) for r in rows}),
        latest_data_date=max((r['data_date'] for r in rows if r['data_date']), default=None),
        nnm_month=_sum(rows, 'nnm_month'), nnm_ytd=_sum(rows, 'nnm_ytd'),
    ), currency)


def _group(rows: list[dict], keys: tuple[str, ...], currency: str) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    return sorted([dict(zip(keys, key), **_aggregate(items, currency)) for key, items in groups.items()],
                  key=lambda r: r['aum'] or 0, reverse=True)


@router.get('/overview')
def industry_overview(
    _: CacheHook, fund_type: FundType = 'all', currency: Currency = 'CLP',
    categoria: str | None = None, tipo: str | None = None, nombre_cat: str | None = None,
    admin: str | None = None, rescatable: bool | None = None,
    include_ytd: bool = True,
) -> dict:
    params = dict(fund_type=fund_type, currency=currency, category=categoria, type=tipo,
                  nombre_cat=nombre_cat, admin=f'%{admin}%' if admin else None,
                  rescatable=rescatable, vigente=True)
    # Read the fund snapshot once, then aggregate its small result in memory.
    rows = _rows(f'{_snapshot_cte(include_returns=False, include_ytd=include_ytd)} SELECT * FROM snapshot {_filters(params)} AND aum IS NOT NULL', params)
    total = _aggregate(rows, currency)
    admins = _group(rows, ('administrator',), currency)
    for a in admins:
        a['market_share_pct'] = round(a['aum'] * 100 / total['aum'], 2) if a['aum'] is not None and total['aum'] else None
    categories = _group(rows, ('fund_type', 'category_type', 'category_group', 'category', 'category_name'), currency)
    for c in categories:
        c['type'] = c.pop('category_type')
        c['group'] = c.pop('category_group')
    fm = [r for r in rows if r['fund_type'] == 'fm']
    return _legacy(dict(
        currency=currency, units='native', includes_ytd=include_ytd, total_aum=total['aum'], active_funds=total['active_funds'],
        administrators=len(admins), latest_data_date=total['latest_data_date'],
        net_flow_month=total['nnm_month'], net_flow_ytd=total['nnm_ytd'],
        aportes_month=_sum(fm, 'aportes_month'), rescates_month=_sum(fm, 'rescates_month'),
        neto_month=_sum(fm, 'nnm_month'),
        flow_coverage='FM adjusted external flows and FI rescatable implied daily flows; non-rescatable FI excluded',
        breakdown=_group(rows, ('fund_type',), currency), top_administrators=admins[:10],
        btg_administrator=next((a for a in admins if 'BTG' in (a['administrator'] or '').upper()), None),
        category_aum_breakdown=categories,
    ), currency)


def _evolution_sources(use_monthly: bool) -> str:
    if use_monthly:
        return """
        fm_monthly AS (
            SELECT run_fondo, month, aum, aportes, rescates, nnm
            FROM mv_industry_monthly_fm
            WHERE :fund_type != 'fi' AND currency = :currency
              AND month >= :from_date
              AND month <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
        ),
        fi_monthly AS (
            SELECT run_fondo, month, aum
            FROM mv_industry_monthly_fi
            WHERE :fund_type != 'fm' AND currency = :currency
              AND month >= :from_date
              AND month <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
        ),
    adjustments AS (
        SELECT target_run_fondo AS run_fondo, DATE_TRUNC('month', event_date)::date AS month,
               SUM(amount) AS amount
        FROM fm_flow_adjustments WHERE status IN ('auto_confirmed', 'confirmed') AND currency = :currency
          AND event_date >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
          AND event_date <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
        GROUP BY target_run_fondo, DATE_TRUNC('month', event_date)::date
    )"""
    return f"""
    fm_daily AS (
        SELECT cd.fecha AS data_date, cd.run_fondo, SUM(cd.patrimonio_neto) AS aum,
               SUM(cd.monto_aportado) AS aportes, SUM(cd.monto_rescatado) AS rescates,
               SUM(cd.monto_aportado - cd.monto_rescatado) AS reported_nnm
        FROM cartola_diaria cd JOIN fondo_mutuo f USING (run_fondo)
        WHERE cd.fecha >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
          AND cd.fecha <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
          AND :fund_type != 'fi' AND {FM_CURRENCY} = :currency
        GROUP BY cd.fecha, cd.run_fondo
    ),
    adjustments AS (
        SELECT target_run_fondo AS run_fondo, DATE_TRUNC('month', event_date)::date AS month,
               SUM(amount) AS amount
        FROM fm_flow_adjustments WHERE status IN ('auto_confirmed', 'confirmed') AND currency = :currency
          AND event_date >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
          AND event_date <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
        GROUP BY target_run_fondo, DATE_TRUNC('month', event_date)::date
    ),
    fm_monthly AS (
        SELECT run_fondo, DATE_TRUNC('month', data_date)::date AS month,
               (ARRAY_AGG(aum ORDER BY data_date DESC))[1] AS aum,
               SUM(aportes) AS aportes, SUM(rescates) AS rescates, SUM(reported_nnm) AS nnm
        FROM fm_daily GROUP BY run_fondo, DATE_TRUNC('month', data_date)::date
    ),
    fi_daily AS (
        SELECT v.fecha AS data_date, v.run_fondo, SUM(v.patrimonio_neto) AS aum
        FROM valores_cuota_fi v JOIN fondos_inversion f USING (run_fondo)
        WHERE v.fecha >= COALESCE(CAST(:from_date AS date), CURRENT_DATE - INTERVAL '1 year')
          AND v.fecha <= COALESCE(CAST(:to_date AS date), CURRENT_DATE)
          AND :fund_type != 'fm' AND {FI_CURRENCY} = :currency
        GROUP BY v.fecha, v.run_fondo
    ),
    fi_monthly AS (
        SELECT run_fondo, DATE_TRUNC('month', data_date)::date AS month,
               (ARRAY_AGG(aum ORDER BY data_date DESC))[1] AS aum
        FROM fi_daily GROUP BY run_fondo, DATE_TRUNC('month', data_date)::date
    )"""


def _monthly_history_is_current(fund_type: str) -> bool:
    checks = []
    for kind, source in (("fm", "cartola_diaria"), ("fi", "valores_cuota_fi")):
        if fund_type not in ("all", kind):
            continue
        checks.append(f"""(SELECT MAX(latest_data_date) FROM mv_industry_monthly_{kind})
            IS NOT DISTINCT FROM (SELECT MAX(fecha) FROM {source} WHERE fecha <= CURRENT_DATE)""")
    return bool(_rows('SELECT ' + ' AND '.join(checks) + ' AS current')[0]['current'])


@router.get('/evolution')
def industry_evolution(
    _: CacheHook, fund_type: FundType = 'all', currency: Currency = 'CLP',
    group_by: GroupBy = 'market', from_date: date | None = None, to_date: date | None = None,
    categoria: str | None = None, tipo: str | None = None, nombre_cat: str | None = None,
    admin: str | None = None, rescatable: bool | None = None,
) -> list[dict]:
    group_expr = {'market': 'fund_type', 'admin': 'administrator', 'category': 'category'}[group_by]
    params = dict(fund_type=fund_type, currency=currency, from_date=from_date, to_date=to_date,
                  category=categoria, type=tipo, nombre_cat=nombre_cat,
                  admin=f'%{admin}%' if admin else None, rescatable=rescatable)
    # Monthly summaries preserve full calendar months. Arbitrary partial date
    # ranges and sources newer than the summaries retain the exact raw query.
    use_monthly = (from_date is not None and from_date.day == 1
                   and (to_date is None or to_date >= date.today())
                   and _monthly_history_is_current(fund_type))
    rows = _rows(f"""WITH
    {_evolution_sources(use_monthly)},
    fm_cat AS (SELECT DISTINCT ON (run_fondo) * FROM categoria_fm_effective ORDER BY run_fondo, periodo DESC),
    fi_cat AS (SELECT DISTINCT ON (run_fondo) * FROM categoria_fi_effective ORDER BY run_fondo, periodo DESC),
    history AS (
        SELECT :currency AS currency, 'fm'::text AS fund_type, x.month, x.aum, x.aportes, x.rescates,
               x.nnm - COALESCE(a.amount, 0) AS nnm,
               f.razon_social_administradora AS administrator, NULL::boolean AS rescatable,
               c.categoria AS category, c.tipo AS category_type, c.nombre_cat AS category_name
        FROM fm_monthly x JOIN fondo_mutuo f USING (run_fondo)
        LEFT JOIN fm_cat c USING (run_fondo) LEFT JOIN adjustments a USING (run_fondo, month)
        UNION ALL
        SELECT :currency, 'fi', x.month, x.aum, NULL, NULL, NULL, f.administrador, f.rescatable,
               c.categoria, c.tipo, c.nombre_cat
        FROM fi_monthly x JOIN fondos_inversion f USING (run_fondo) LEFT JOIN fi_cat c USING (run_fondo)
    ),
    grouped AS (
        SELECT month AS date, fund_type, COALESCE({group_expr}, 'Sin clasificar') AS group_key,
               SUM(aum) AS aum, SUM(aportes) AS aportes, SUM(rescates) AS rescates, SUM(nnm) AS nnm
        FROM history {_filters(params)}
        GROUP BY month, fund_type, COALESCE({group_expr}, 'Sin clasificar')
    )
    SELECT *, :currency AS currency,
           ROUND(aum * 100.0 / NULLIF(SUM(aum) OVER (PARTITION BY date), 0), 2) AS market_share_pct
    FROM grouped ORDER BY date, aum DESC NULLS LAST
    """, params)
    return [_legacy(r, currency) for r in rows]
