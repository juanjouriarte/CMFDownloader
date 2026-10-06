"""Conservative NAV-implied FI flows; never a reported cash-flow measure."""
from datetime import timedelta
from src.api.industry import _currency


def flow_period_data(currency, admin, end, category, periods, fund_runs=None):
    from .router import rows
    start = min(s for _, s in periods)
    params = dict(currency=currency, admin=admin, start=start, end=end,
                  lookback=start-timedelta(days=7), category=category)
    if fund_runs is not None:params['fund_runs']=fund_runs
    windows = []
    for i, (label, beginning) in enumerate(periods):
        params[f'period_{i}'] = label
        params[f'start_{i}'] = beginning
        windows.append(f'(:period_{i}, CAST(:start_{i} AS date))')
    # Market-wide queries first select funds that actually report this currency.
    # Their subsequent series history still includes ALL currencies, preserving
    # the checks against bridging a currency transition.
    candidates = (f"""AND f.run_fondo IN (SELECT v.run_fondo FROM valores_cuota_fi v
        WHERE v.fecha BETWEEN :lookback AND :end AND {_currency('v.moneda')}=:currency)"""
        if admin is None and fund_runs is None else '')
    # Read all source currencies before LAG, so a currency transition cannot be
    # mistaken for a flow. Unknown source currencies never inherit today's code.
    return rows(f"""WITH windows(period,start_date) AS (VALUES {','.join(windows)}),
      eligible AS MATERIALIZED (
        SELECT f.run_fondo, f.razon_social name, COALESCE(c.nombre_cat,'Sin clasificación') category
        FROM fondos_inversion f
        LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_fi_effective
          WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
        WHERE f.vigente IS TRUE
          {candidates}
          {'AND f.run_fondo=ANY(:fund_runs)' if fund_runs is not None else ''}
          {"AND COALESCE(f.administrador,'Sin administradora')=:admin" if admin is not None else ''}
          AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)
      ),
      source AS MATERIALIZED (
        SELECT v.run_fondo, v.serie, v.fecha, {_currency('v.moneda')} currency,
          v.valor_libro price,
          CASE WHEN v.valor_libro>0 AND v.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
            AND v.patrimonio_neto>=0 THEN v.patrimonio_neto::numeric/v.valor_libro END units,
          v.patrimonio_neto IS NOT NULL AND v.patrimonio_neto>=0 has_nav
        FROM valores_cuota_fi v JOIN eligible f USING(run_fondo)
        WHERE v.fecha BETWEEN :lookback AND :end
      ), paired AS (
        SELECT *, LAG(fecha) OVER w previous_date, LAG(currency) OVER w previous_currency,
          LAG(units) OVER w previous_units
        FROM source WINDOW w AS (PARTITION BY run_fondo,serie ORDER BY fecha)
      ), daily AS (
        SELECT run_fondo,fecha,currency,
          ARRAY_AGG(serie ORDER BY serie) series,
          BOOL_OR(has_nav) has_nav,
          BOOL_AND(units IS NOT NULL AND previous_units IS NOT NULL
            AND currency IS NOT DISTINCT FROM previous_currency) valid_values,
          MIN(previous_date) earliest_base, MAX(previous_date) latest_base,
          SUM((units-previous_units)*price) estimate
        FROM paired WHERE currency=:currency GROUP BY run_fondo,fecha,currency
      ), snapshots AS (
        SELECT *, LAG(fecha) OVER w previous_snapshot, LAG(series) OVER w previous_series
        FROM daily WINDOW w AS (PARTITION BY run_fondo,currency ORDER BY fecha)
      ), checked AS (
        SELECT *, CASE
          WHEN previous_snapshot IS NULL THEN 'missing_base'
          WHEN fecha-previous_snapshot>7 THEN 'gap'
          WHEN series IS DISTINCT FROM previous_series THEN 'series_change'
          WHEN NOT valid_values OR earliest_base IS DISTINCT FROM previous_snapshot
            OR latest_base IS DISTINCT FROM previous_snapshot THEN 'incomparable_nav'
          END exclusion
        FROM snapshots WHERE currency=:currency AND fecha>=:start
      )
      SELECT w.period, o.run_fondo run, f.name, f.category,
        MIN(o.fecha) first_date, MAX(o.fecha) last_date,
        NULL::numeric aportes, NULL::numeric rescates,
        SUM(o.estimate) FILTER(WHERE o.exclusion IS NULL) reported,
        0::numeric migrations, COUNT(*) observations,
        COUNT(*) FILTER(WHERE o.exclusion IS NULL) reported_observations,
        COUNT(*) FILTER(WHERE o.exclusion='missing_base') missing_base,
        COUNT(*) FILTER(WHERE o.exclusion='gap') gaps,
        COUNT(*) FILTER(WHERE o.exclusion='series_change') series_changes,
        COUNT(*) FILTER(WHERE o.exclusion='incomparable_nav') incomparable_nav
      FROM checked o JOIN windows w ON o.fecha>=w.start_date JOIN eligible f USING(run_fondo)
      GROUP BY w.period,o.run_fondo,f.name,f.category
      HAVING BOOL_OR(o.has_nav)""", params)
