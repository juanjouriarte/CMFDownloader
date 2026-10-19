"""Currency-scoped FM series flows and a comparable, ex-fund category cohort."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from src.api.classification_admin import classification_cache_epoch
from src.api.industry import FM_CURRENCY

router = APIRouter(prefix='/fund-flows')
Period = Literal['1D', '1W', '1M', '3M', '6M', '1A', '3A', '5A', 'YTD']
DAY = timedelta(days=1)
MIN_DATE = date(2020, 1, 1)


def value(v):
    if v is None:
        return None
    n = Decimal(str(v))
    return n if n.is_finite() else None


def known_sum(values):
    values = [v for v in values if v is not None]
    return sum(values, Decimal(0)) if values else None


def period_start(period, end):
    if period == 'YTD':
        return end.replace(month=1, day=1)
    if period in ('1D', '1W'):
        return end - timedelta(days=0 if period == '1D' else 6)
    months = {'1M': 1, '3M': 3, '6M': 6, '1A': 12, '3A': 36, '5A': 60}[period]
    y, m = divmod(end.year * 12 + end.month - 1 - months, 12)
    return date(y, m + 1, min(end.day, monthrange(y, m + 1)[1])) + DAY


def bucket_date(d, granularity):
    return d if granularity == 'daily' else d - timedelta(days=d.weekday()) if granularity == 'weekly' else d.replace(day=1)


def normalize(rows):
    result = []
    for r in rows:
        a, b, nav = (value(r.get(k)) for k in ('aportes', 'rescates', 'aum'))
        valid = a is not None and b is not None and a >= 0 and b >= 0
        result.append(dict(r, aportes=a if valid else None, rescates=b if valid else None,
                           net=a-b if valid else None, aum=nav if nav is not None and nav >= 0 else None))
    return result


def snapshot(records, target):
    dates = [r['date'] for r in records if target-timedelta(days=7) <= r['date'] <= target]
    if not dates:
        return dict(date=None, aum=None)
    d = max(dates)
    values = [r['aum'] for r in records if r['date'] == d]
    return dict(date=d, aum=known_sum(values) if all(v is not None for v in values) else None)


def confirmed_start(inception, first_report):
    # A first downloaded row is not evidence of inception. Conflicting earlier
    # reports also prevent us from hiding dates behind a later registry date.
    return inception if inception and (first_report is None or first_report >= inception) else None


def expected_dates(start, end, inception=None):
    return max(0, (end-max(start, inception or start)).days+1)


def summary(records, start, end, adjustments=None, inception=None):
    selected = [r for r in records if start <= r['date'] <= end]
    valid = [r for r in selected if r['net'] is not None]
    reported = known_sum(r['net'] for r in valid)
    migrations = sum((amount for d, amount in (adjustments or {}).items()
                      if start <= d <= end and any(r['date'] == d for r in valid)), Decimal(0))
    net = reported-migrations if reported is not None else None
    initial, final = snapshot(records, start-DAY), snapshot(records, end)
    days = len({r['date'] for r in selected})
    expected = expected_dates(start, end, inception)
    complete = bool(selected) and days == expected and len(valid) == len(selected)
    # Never turn a partial stream or missing/zero opening NAV into a flow rate.
    ratio = net/initial['aum']*100 if complete and expected == (end-start).days+1 and net is not None and initial['aum'] and initial['aum'] > 0 else None
    return dict(aportes=known_sum(r['aportes'] for r in valid), rescates=known_sum(r['rescates'] for r in valid),
                reported=reported, migrations=migrations if reported is not None else None, net=net,
                initial_aum=initial['aum'], initial_date=initial['date'], final_aum=final['aum'], final_date=final['date'],
                rate=ratio, observations=len(selected), valid_observations=len(valid), days=days,
                expected_days=expected, complete=complete, inception_date=inception,
                pre_inception_days=(end-start).days+1-expected,
                first_date=min((r['date'] for r in selected), default=None), last_date=max((r['date'] for r in selected), default=None))


def timeline(records, start, end, granularity, adjustments=None, inception=None):
    groups = defaultdict(list)
    for r in records:
        if start <= r['date'] <= end:
            groups[bucket_date(r['date'], granularity)].append(r)
    initial = snapshot(records, start-DAY)['aum']
    cumulative = Decimal(0)
    intact = True
    points = []
    cursor = start
    while cursor <= end:
        key = bucket_date(cursor, granularity)
        following = cursor+DAY if granularity == 'daily' else key+timedelta(days=7) if granularity == 'weekly' else date(key.year+key.month//12, key.month%12+1, 1)
        last = min(end, following-DAY)
        part = summary(groups[key], cursor, last, adjustments, inception)
        intact = intact and part['complete']
        if part['net'] is not None:
            cumulative += part['net']
        points.append(dict(date=cursor, end=last, net=part['net'], aportes=part['aportes'], rescates=part['rescates'],
                           complete=part['complete'], cumulative_rate=cumulative/initial*100 if intact and initial and initial > 0 else None))
        cursor = following
    return points


def analyze(records, adjustments, start, end, inception=None):
    previous_end = start-DAY
    previous_start = start-timedelta(days=(end-start).days+1)
    year_start = end.replace(month=1, day=1)
    granularity = 'daily' if (end-start).days < 45 else 'weekly' if (end-start).days < 180 else 'monthly'
    months = [(date(end.year, m, 1), min(end, date(end.year, m, monthrange(end.year, m)[1]))) for m in range(1, end.month+1)]
    def make(serie, entries, adjustments=None):
        current = summary(entries, start, end, adjustments, inception)
        previous = summary(entries, previous_start, previous_end, adjustments, inception)
        delta = current['net']-previous['net'] if current['complete'] and previous['complete'] and not current['pre_inception_days'] and not previous['pre_inception_days'] and current['net'] is not None and previous['net'] is not None else None
        return dict(serie=serie, **current, previous=previous, change=delta,
                    timeline=timeline(entries, start, end, granularity, adjustments, inception),
                    monthly=[dict(month=a.month, **summary(entries, a, b, adjustments, inception)) for a, b in months],
                    ytd=summary(entries, year_start, end, adjustments, inception))
    by_series = defaultdict(list)
    for r in records:
        by_series[r['serie']].append(r)
    return dict(total=make(None, records, adjustments), series=[make(s, rs) for s, rs in sorted(by_series.items(), key=lambda x: x[0] or '')],
                granularity=granularity, previous_start=previous_start, previous_end=previous_end)


@lru_cache(maxsize=24)
def load(run, currency, period, requested_start, requested_end, epoch):
    from .router import rows
    meta = rows(f"""SELECT f.nombre_fondo name, c.nombre_cat category, c.tipo, c.grupo,
      MIN(cd.fecha) first_date, MAX(cd.fecha) latest, f.fecha_inicio_operaciones inception_date
      FROM fondo_mutuo f JOIN cartola_diaria cd USING(run_fondo)
      LEFT JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_fm_effective
        WHERE run_fondo=f.run_fondo AND periodo<=CURRENT_DATE ORDER BY periodo DESC LIMIT 1) c ON true
      WHERE f.run_fondo=:run AND {FM_CURRENCY}=:currency AND cd.fecha<=CURRENT_DATE
      GROUP BY f.nombre_fondo,c.nombre_cat,c.tipo,c.grupo,f.fecha_inicio_operaciones""", dict(run=run, currency=currency))
    if not meta or meta[0]['latest'] is None:
        raise HTTPException(404, 'Sin cartola para este fondo y moneda.')
    info = meta[0]
    end = min(requested_end or info['latest'], info['latest'])
    start = requested_start or max(MIN_DATE, period_start(period, end))
    if start > end:
        raise HTTPException(422, 'El rango no contiene fechas disponibles del fondo.')
    previous_start = start-timedelta(days=(end-start).days+1)
    scan_start = max(MIN_DATE-timedelta(days=7), min(previous_start, end.replace(month=1, day=1))-timedelta(days=8))
    params = dict(run=run, currency=currency, start=start, end=end, scan_start=scan_start,
                  previous_start=previous_start, previous_end=start-DAY,
                  category=info['category'], tipo=info['tipo'], grupo=info['grupo'])
    records = normalize(rows(f"""SELECT cd.serie,cd.fecha date,cd.monto_aportado aportes,
        cd.monto_rescatado rescates,cd.patrimonio_neto aum
        FROM cartola_diaria cd JOIN fondo_mutuo f USING(run_fondo)
        WHERE cd.run_fondo=:run AND cd.fecha BETWEEN :scan_start AND :end
        AND {FM_CURRENCY}=:currency ORDER BY cd.fecha,cd.serie""", params))
    adjustments = {r['date']:value(r['amount']) for r in rows("""SELECT event_date date,SUM(amount) amount FROM fm_flow_adjustments
      WHERE target_run_fondo=:run AND currency=:currency AND event_date BETWEEN :scan_start AND :end
      AND status IN ('confirmed','auto_confirmed') GROUP BY event_date""", params)}
    analysis = analyze(records, adjustments, start, end, confirmed_start(info['inception_date'], info['first_date']))
    peers = category_peers(params) if info['category'] else []
    comparable = [p for p in peers if p['rate'] is not None]
    cohort = [p['run'] for p in comparable]
    category = dict(name=info['category'], peers=peers, funds=len(peers), comparable_funds=len(comparable),
                    net=known_sum(p['net'] for p in comparable), initial_aum=known_sum(p['initial_aum'] for p in comparable))
    category['rate'] = category['net']/category['initial_aum']*100 if category['initial_aum'] else None
    category['timeline'] = category_timeline(params, cohort, analysis['granularity'], category['initial_aum']) if cohort else []
    category['calendar'] = category_calendar(params)
    return dict(run=run, currency=currency, name=info['name'], start=start, end=end,
                latest=info['latest'], first_date=max(MIN_DATE, info['first_date']), requested_end=requested_end,
                category=category, **analysis)


def calendar_totals(records, end):
    """Absolute category flows: every observed peer, independent of opening NAV."""
    registry = {r['run']: r for r in records}
    starts = {run: confirmed_start(r.get('inception_date'), r.get('history_start')) for run, r in registry.items()}

    def aggregate(items, start, stop):
        net = known_sum(value(r['net']) for r in items)
        observations = sum(r['observations'] for r in items)
        valid = sum(r['valid_observations'] for r in items)
        days = sum(r['days'] for r in items)
        expected_days = sum(expected_dates(start, stop, inception) for inception in starts.values())
        funds = sum(expected_dates(start, stop, inception) > 0 for inception in starts.values())
        started = [dict(run=run, name=registry[run].get('name', run), date=inception,
                        excluded_days=(stop-start).days+1-expected_dates(start, stop, inception))
                   for run, inception in starts.items() if inception and inception > start]
        known_funds = len({r['run'] for r in items if r['net'] is not None})
        return dict(net=net, reported=known_sum(value(r['reported']) for r in items),
                    migrations=known_sum(value(r['migrations']) for r in items if r['net'] is not None),
                    observations=observations, valid_observations=valid,
                    days=days, expected_days=expected_days, funds=funds, known_funds=known_funds,
                    pre_inception_days=sum(f['excluded_days'] for f in started), started_funds=started,
                    complete=net is not None and valid == observations and days == expected_days and known_funds == funds,
                    first_date=min((r['first_date'] for r in items), default=None),
                    last_date=max((r['last_date'] for r in items), default=None), start=start, end=stop)

    monthly = []
    for month in range(1, end.month+1):
        start = date(end.year, month, 1)
        stop = min(end, date(end.year, month, monthrange(end.year, month)[1]))
        monthly.append(dict(month=month, **aggregate([r for r in records if r['month'] == start], start, stop)))
    return dict(monthly=monthly, ytd=aggregate(records, end.replace(month=1, day=1), end))


def category_calendar(params):
    from .router import rows
    if not params['category']:
        return calendar_totals([], params['end'])
    records = rows("""WITH category AS MATERIALIZED (
      SELECT f.run_fondo,f.nombre_fondo name,f.fecha_inicio_operaciones inception_date FROM fondo_mutuo f
      JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_fm_effective
        WHERE run_fondo=f.run_fondo AND periodo<=CURRENT_DATE ORDER BY periodo DESC LIMIT 1) c ON true
      WHERE f.fecha_termino_operaciones IS NULL AND f.run_fondo<>:run
        AND c.nombre_cat=:category AND c.tipo IS NOT DISTINCT FROM :tipo AND c.grupo IS NOT DISTINCT FROM :grupo
    ), observations AS MATERIALIZED (
      SELECT d.* FROM mv_nnm_daily_fm d JOIN category c USING(run_fondo)
      WHERE d.currency=:currency AND d.fecha BETWEEN :year_start AND :end
    ), included AS MATERIALIZED (
      SELECT c.*,h.history_start FROM category c
      JOIN (SELECT run_fondo FROM observations GROUP BY run_fondo HAVING BOOL_OR(has_nav)) o USING(run_fondo)
      LEFT JOIN LATERAL (SELECT d.fecha history_start FROM mv_nnm_daily_fm d
        WHERE d.run_fondo=c.run_fondo AND d.currency=:currency ORDER BY d.fecha LIMIT 1) h ON true
    ), adjustments AS (
      SELECT target_run_fondo run_fondo,event_date fecha,SUM(amount) amount FROM fm_flow_adjustments
      WHERE currency=:currency AND event_date BETWEEN :year_start AND :end
        AND status IN ('confirmed','auto_confirmed') GROUP BY target_run_fondo,event_date
    ) SELECT d.run_fondo run,DATE_TRUNC('month',d.fecha)::date AS month,
      i.name,i.inception_date,i.history_start,
      SUM(d.reported) reported,
      SUM(CASE WHEN d.reported_observations>0 THEN COALESCE(a.amount,0) END) migrations,
      SUM(d.reported-CASE WHEN d.reported_observations>0 THEN COALESCE(a.amount,0) END) net,
      SUM(d.observations) observations,SUM(d.reported_observations) valid_observations,
      COUNT(*) days,MIN(d.fecha) first_date,MAX(d.fecha) last_date
    FROM observations d JOIN included i USING(run_fondo) LEFT JOIN adjustments a USING(run_fondo,fecha)
    GROUP BY d.run_fondo,DATE_TRUNC('month',d.fecha),i.name,i.inception_date,i.history_start""", dict(params, year_start=params['end'].replace(month=1, day=1)))
    return calendar_totals(records, params['end'])


def category_peers(params):
    from .router import rows
    # Indexed fact reads for the category only, plus one 7-day NAV boundary read per peer.
    result = rows(f"""WITH category AS MATERIALIZED (
        SELECT f.run_fondo,f.nombre_fondo name,f.razon_social_administradora admin FROM fondo_mutuo f
        JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_fm_effective
          WHERE run_fondo=f.run_fondo AND periodo<=CURRENT_DATE ORDER BY periodo DESC LIMIT 1) c ON true
        WHERE f.fecha_termino_operaciones IS NULL AND f.run_fondo<>:run
          AND c.nombre_cat=:category AND c.tipo IS NOT DISTINCT FROM :tipo AND c.grupo IS NOT DISTINCT FROM :grupo
      ), totals AS (
        SELECT d.run_fondo,SUM(d.aportes) aportes,SUM(d.rescates) rescates,SUM(d.reported) reported,
          SUM(d.observations) observations,SUM(d.reported_observations) valid_observations,
          COUNT(*) days,MIN(d.fecha) first_date,MAX(d.fecha) last_date
        FROM category c JOIN mv_nnm_daily_fm d USING(run_fondo)
        WHERE d.currency=:currency AND d.fecha BETWEEN :start AND :end
        GROUP BY d.run_fondo HAVING BOOL_OR(d.has_nav)
      ), adjustments AS (
        SELECT a.target_run_fondo run_fondo,SUM(a.amount) amount FROM fm_flow_adjustments a
        JOIN category c ON c.run_fondo=a.target_run_fondo
        WHERE a.currency=:currency AND a.event_date BETWEEN :start AND :end AND a.status IN ('confirmed','auto_confirmed')
          AND EXISTS(SELECT 1 FROM mv_nnm_daily_fm d WHERE d.run_fondo=a.target_run_fondo AND d.currency=a.currency
            AND d.fecha=a.event_date AND d.reported_observations>0)
        GROUP BY a.target_run_fondo
      ) SELECT c.run_fondo run,c.name,c.admin,t.*,COALESCE(a.amount,0) migrations,b.aum initial_aum,b.date initial_date
      FROM category c JOIN totals t USING(run_fondo) LEFT JOIN adjustments a USING(run_fondo)
      LEFT JOIN LATERAL (
        SELECT cd.fecha date,CASE WHEN BOOL_AND(cd.patrimonio_neto>=0 AND cd.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')
          AND cd.patrimonio_neto IS NOT NULL) THEN SUM(cd.patrimonio_neto) END aum
        FROM cartola_diaria cd JOIN fondo_mutuo f USING(run_fondo)
        WHERE cd.run_fondo=c.run_fondo AND cd.fecha BETWEEN CAST(:start AS date)-8 AND CAST(:start AS date)-1
          AND {FM_CURRENCY}=:currency GROUP BY cd.fecha ORDER BY cd.fecha DESC LIMIT 1
      ) b ON true ORDER BY c.name""", params)
    expected = (params['end']-params['start']).days+1
    for r in result:
        for key in ('aportes','rescates','reported','migrations','initial_aum'):
            r[key] = value(r[key])
        r['net'] = r['reported']-r['migrations'] if r['reported'] is not None else None
        r['complete'] = r['days'] == expected and r['observations'] == r['valid_observations']
        r['rate'] = r['net']/r['initial_aum']*100 if r['complete'] and r['net'] is not None and r['initial_aum'] and r['initial_aum']>0 else None
        del r['run_fondo']
    return result


def category_timeline(params, cohort, granularity, initial):
    from .router import rows
    daily = rows("""WITH adjustments AS (
      SELECT target_run_fondo run_fondo,event_date fecha,SUM(amount) amount FROM fm_flow_adjustments
      WHERE target_run_fondo=ANY(:cohort) AND currency=:currency AND event_date BETWEEN :start AND :end
        AND status IN ('confirmed','auto_confirmed') GROUP BY target_run_fondo,event_date
    ) SELECT d.fecha date,SUM(d.aportes) aportes,SUM(d.rescates) rescates,
      SUM(d.reported-COALESCE(a.amount,0)) net
    FROM mv_nnm_daily_fm d LEFT JOIN adjustments a USING(run_fondo,fecha)
    WHERE d.run_fondo=ANY(:cohort) AND d.currency=:currency AND d.fecha BETWEEN :start AND :end
    GROUP BY d.fecha ORDER BY d.fecha""", dict(params, cohort=cohort))
    groups = defaultdict(list)
    for r in daily:
        groups[bucket_date(r['date'], granularity)].append(r)
    result, cumulative = [], Decimal(0)
    for _, rs in sorted(groups.items()):
        net = known_sum(value(r['net']) for r in rs)
        cumulative += net or Decimal(0)
        result.append(dict(date=max(params['start'], bucket_date(rs[0]['date'], granularity)),
                           net=net, cumulative_rate=cumulative/initial*100 if initial else None))
    return result


@router.get('/fm/{run}')
def fund_flows(run: str, currency: str=Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'),
               period: Period='1M', from_date: date|None=None, to_date: date|None=None):
    if (from_date is None) != (to_date is None):
        raise HTTPException(422, 'Indique ambas fechas para un rango personalizado.')
    if from_date and (from_date < MIN_DATE or from_date > to_date or to_date > date.today()):
        raise HTTPException(422, 'Seleccione fechas entre 2020 y hoy, en orden cronológico.')
    return load(run, currency, period, from_date, to_date, classification_cache_epoch())
