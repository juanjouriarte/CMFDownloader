"""Ranking windows over refreshed daily facts; registry and adjustments stay live."""
from datetime import timedelta


def flow_data(kind, currency, start, end, periods=None):
    from .router import rows
    if kind not in ('fm', 'fi'):
        raise ValueError('Unknown fund kind')
    periods = periods or [('ranking', start)]
    params = dict(currency=currency, start=start, end=end,
                  lookback=start-timedelta(days=7))
    windows = []
    for i, (label, beginning) in enumerate(periods):
        params[f'period_{i}'], params[f'start_{i}'] = label, beginning
        windows.append(f'(:period_{i}, CAST(:start_{i} AS date))')
    registry, name, active = (
        ('fondo_mutuo', 'nombre_fondo', 'f.fecha_termino_operaciones IS NULL') if kind == 'fm'
        else ('fondos_inversion', 'razon_social', 'f.vigente IS TRUE'))
    ctes = f"""WITH windows(period,start_date) AS (VALUES {','.join(windows)}),
      eligible AS MATERIALIZED (
        SELECT f.run_fondo,f.{name} name,COALESCE(c.nombre_cat,'Sin clasificación') category
        FROM {registry} f LEFT JOIN (
          SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_{kind}_effective
          WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC
        ) c USING(run_fondo) WHERE {active}
      ), observations AS NOT MATERIALIZED (
        SELECT d.*{", CASE WHEN previous_snapshot IS NULL OR previous_snapshot<:lookback THEN 'missing_base' ELSE exclusion END reason" if kind=='fi' else ''}
        FROM mv_nnm_daily_{kind} d JOIN eligible f USING(run_fondo)
        WHERE d.currency=:currency AND d.fecha BETWEEN :start AND :end
      )"""
    if kind == 'fm':
        query = ctes + """, adjustments AS (
          SELECT a.target_run_fondo run_fondo,w.period,SUM(a.amount) amount
          FROM fm_flow_adjustments a JOIN windows w ON a.event_date>=w.start_date
          WHERE a.currency=:currency AND a.status IN ('confirmed','auto_confirmed')
            AND a.event_date BETWEEN :start AND :end
            AND EXISTS(SELECT 1 FROM mv_nnm_daily_fm o WHERE o.run_fondo=a.target_run_fondo
                       AND o.fecha=a.event_date AND o.currency=a.currency AND o.reported_observations>0)
          GROUP BY a.target_run_fondo,w.period
        ), totals AS (
          SELECT w.period,o.run_fondo,
            MIN(o.fecha) first_date,MAX(o.fecha) last_date,
            SUM(o.aportes) aportes,SUM(o.rescates) rescates,SUM(o.reported) reported,
            SUM(o.observations) observations,SUM(o.reported_observations) reported_observations
          FROM observations o JOIN windows w ON o.fecha>=w.start_date
          GROUP BY w.period,o.run_fondo HAVING BOOL_OR(o.has_nav)
        ) SELECT t.*,t.run_fondo run,f.name,f.category,
          CASE WHEN t.reported_observations>0 THEN COALESCE(a.amount,0) END migrations
        FROM totals t JOIN eligible f USING(run_fondo)
        LEFT JOIN adjustments a USING(run_fondo,period)"""
    else:
        query = ctes + """, totals AS (
          SELECT w.period,o.run_fondo,
            MIN(o.fecha) first_date,MAX(o.fecha) last_date,
            NULL::numeric aportes,NULL::numeric rescates,
            SUM(o.estimate) FILTER(WHERE o.reason IS NULL) reported,0::numeric migrations,
            COUNT(*) observations,COUNT(*) FILTER(WHERE o.reason IS NULL) reported_observations,
            COUNT(*) FILTER(WHERE o.reason='missing_base') missing_base,
            COUNT(*) FILTER(WHERE o.reason='gap') gaps,
            COUNT(*) FILTER(WHERE o.reason='series_change') series_changes,
            COUNT(*) FILTER(WHERE o.reason='incomparable_nav') incomparable_nav
          FROM observations o JOIN windows w ON o.fecha>=w.start_date
          GROUP BY w.period,o.run_fondo HAVING BOOL_OR(o.has_nav)
        ) SELECT t.*,t.run_fondo run,f.name,f.category FROM totals t JOIN eligible f USING(run_fondo)"""
    # Keep the source-query contract used by the ranking response builders.
    return [{k:v for k,v in row.items() if k!='run_fondo'} for row in rows(query, params)]
