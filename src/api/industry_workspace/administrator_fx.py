"""CLP views of the existing active, NAV-backed administrator universe."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

from src.fx import Rates, MAX_AGE_DAYS


def monthly_samples(data, rates):
    """Choose the common date before FX; never pick an older date to hide missing FX."""
    closes = {}
    for r in data:
        key = (r['kind'], r['run_fondo'], r['month'])
        closes[key] = max(closes.get(key, r['latest_data_date']), r['latest_data_date'])
    months = defaultdict(list)
    for r in data:
        if r['latest_data_date'] == closes[(r['kind'], r['run_fondo'], r['month'])]:
            months[r['month']].append(r)
    converted, coverage = [], {}
    for month, available in sorted(months.items()):
        by_date = defaultdict(list)
        for r in available:
            by_date[r['latest_data_date']].append(r)
        keys = lambda rows: {(r['kind'], r['run_fondo']) for r in rows}
        ref = max(by_date, key=lambda d: (len({r['kind'] for r in by_date[d]}), len(keys(by_date[d])), d))
        sample = by_date[ref]
        funds, details, missing = {}, {}, set()
        for r in sample:
            key = (r['kind'], r['run_fondo'])
            rate = rates.get(r['currency'], ref)
            fund = funds.setdefault(key, dict(r, aum=Decimal(0), native_currencies=set()))
            fund['native_currencies'].add(r['currency'])
            if rate:
                fund['aum'] += Decimal(str(r['aum'])) * rate['clp_per_unit']
            else:
                missing.add(key)
            detail = details.setdefault(r['currency'], dict(currency=r['currency'], nav_date=ref,
                rate_date=rate['date'] if rate else None,
                rate=rate['clp_per_unit'] if rate else None, funds=set()))
            detail['funds'].add(key)
        eligible, reported = len(keys(available)), len(funds)
        coverage[month] = dict(date=ref, eligible_funds=eligible, reported_funds=reported,
            coverage_pct=reported / eligible * 100 if eligible else None,
            missing_fx_funds=len(missing),
            fx=[dict(d, funds=len(d['funds'])) for _, d in sorted(details.items(), key=lambda x:x[0] or '')])
        # No partial market ranking or change is presented as a complete total.
        if not missing:
            for fund in funds.values():
                currencies = sorted(fund.pop('native_currencies'), key=lambda x:x or '')
                fund['native_currency'] = currencies[0] if len(currencies) == 1 else None
                converted.append(fund)
    return converted, coverage


@lru_cache(maxsize=32)
def load_analysis(kind, period, category, bucket):
    from .router import monthly_data, rows
    from .administrators import build_analysis, month_shift
    tables = [f'mv_industry_monthly_{k}' for k in ('fm', 'fi') if kind in ('all', k)]
    periods = [r['month'] for r in rows('SELECT DISTINCT month FROM (' + ' UNION ALL '.join(
        f"SELECT month FROM {table} WHERE aum IS NOT NULL AND month BETWEEN '2020-01-01' AND CURRENT_DATE"
        for table in tables) + ') p ORDER BY month DESC')]
    selected = period or (periods[0] if periods else date.today().replace(day=1))
    raw = monthly_data(None, kind, month_shift(selected, -12), selected, active_only=True)
    categories = sorted({r['category'] for r in raw if r['month'] == selected})
    raw = [r for r in raw if not category or r['category'] == category]
    rates = Rates.load(min((r['latest_data_date'] for r in raw), default=None),
                       max((r['latest_data_date'] for r in raw), default=None))
    converted, coverage = monthly_samples(raw, rates)
    result = build_analysis(converted, selected, 'CLP', kind, category)
    for snap in result['history']:
        snap.update(coverage.get(snap['month'], dict(missing_fx_funds=0, fx=[])))
    current = result['history'][-1]
    for key in ('date', 'eligible_funds', 'reported_funds', 'coverage_pct', 'missing_fx_funds', 'fx'):
        result[key] = current[key]
    if current['missing_fx_funds']:
        # Lack of conversion is not a missing fund filing or a departure.
        result['funds'] = []
    result['previous_date'] = result['history'][-2]['date']
    result['year_date'] = result['history'][0]['date']
    result.update(mode='consolidated', categories=categories, periods=periods, max_fx_age_days=MAX_AGE_DAYS)
    return result


@lru_cache(maxsize=32)
def load_history(kind, admin, start, end, category, metric, bucket):
    from .router import rows, monthly_data, market_snapshots
    from src.api.industry import FM_CURRENCY, FI_CURRENCY
    if (end-start).days > 400:
        end_month = end.replace(day=1)
        raw = monthly_data(None, kind, start.replace(day=1), end_month-timedelta(days=1), category, active_only=True)
        raw = [r for r in raw if start <= r['latest_data_date'] < end_month and (metric == 'share' or r['admin'] == admin)]
        converted, coverage = monthly_samples(raw, Rates.load(start, end))
        snapshots = {s['month']: s for s in market_snapshots(converted)}
        points = []
        for month, meta in sorted(coverage.items()):
            snap = snapshots.get(month, {})
            own = next((a for a in snap.get('administrators', []) if a['admin'] == admin), None)
            points.append(dict(date=meta['date'], aum=own['aum'] if own else None,
                share=own['share'] if own and metric == 'share' else None,
                funds=own['funds'] if own else 0,
                market_funds=meta['reported_funds'] if metric == 'share' else None,
                missing_fx_funds=meta['missing_fx_funds']))
        boundary = load_history(kind, admin, end_month, end, category, metric, bucket)['points']
        if boundary:
            points.append(boundary[-1])
        return dict(currency='CLP', mode='consolidated', granularity='monthly', points=points)
    parts = []
    for k, table, fund_table, alias, name, active, expression in [
        ('fm','cartola_diaria','fondo_mutuo','cd','razon_social_administradora','f.fecha_termino_operaciones IS NULL',FM_CURRENCY),
        ('fi','valores_cuota_fi','fondos_inversion','v','administrador','f.vigente IS TRUE',FI_CURRENCY),
    ]:
        if kind not in ('all', k):
            continue
        parts.append(f"""SELECT '{k}' kind,{alias}.run_fondo,{alias}.fecha date,
            {expression} currency,COALESCE(f.{name},'Sin administradora') admin,
            SUM({alias}.patrimonio_neto) aum
            FROM {table} {alias} JOIN {fund_table} f USING(run_fondo)
            LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_{k}_effective
                WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
            WHERE {alias}.fecha BETWEEN :start AND :end AND {active}
                AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)
                {'AND COALESCE(f.'+name+",'Sin administradora')=:admin" if metric == 'aum' else ''}
                AND {alias}.patrimonio_neto IS NOT NULL AND {alias}.patrimonio_neto>=0
                AND {alias}.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')
            GROUP BY {alias}.run_fondo,{alias}.fecha,f.{name},{expression}""")
    data = rows('WITH daily AS ('+' UNION ALL '.join(parts)+'''),
        rates AS MATERIALIZED (SELECT currency,date,clp_per_unit FROM fx_daily WHERE date BETWEEN :fx_start AND :end),
        pairs AS (SELECT DISTINCT currency,date FROM daily),
        conversion AS (SELECT DISTINCT ON(p.currency,p.date) p.currency,p.date,
            CASE WHEN p.currency='CLP' THEN 1 ELSE r.clp_per_unit END rate
            FROM pairs p LEFT JOIN rates r ON r.currency=p.currency
                AND r.date BETWEEN p.date-:max_age AND p.date
            ORDER BY p.currency,p.date,r.date DESC),
        valued AS (SELECT d.*,c.rate FROM daily d LEFT JOIN conversion c
            ON c.currency IS NOT DISTINCT FROM d.currency AND c.date=d.date)
        SELECT date,
            CASE WHEN COUNT(*) FILTER(WHERE admin=:admin AND rate IS NULL)=0
                THEN SUM(aum*rate) FILTER(WHERE admin=:admin) END aum,
            CASE WHEN COUNT(*) FILTER(WHERE rate IS NULL)=0 THEN SUM(aum*rate) END market_aum,
            COUNT(DISTINCT(kind,run_fondo)) FILTER(WHERE admin=:admin) funds,
            COUNT(DISTINCT(kind,run_fondo)) market_funds,
            COUNT(DISTINCT(kind,run_fondo)) FILTER(WHERE rate IS NULL) missing_fx_funds
        FROM valued GROUP BY date ORDER BY date''', dict(start=start, end=end, admin=admin, category=category,
            fx_start=start-timedelta(days=MAX_AGE_DAYS), max_age=MAX_AGE_DAYS))
    points = [dict(date=r['date'], aum=float(r['aum']) if r['aum'] is not None else None,
        share=float(r['aum']/r['market_aum']*100) if metric=='share' and r['aum'] is not None and r['market_aum'] else None,
        funds=r['funds'], market_funds=r['market_funds'] if metric=='share' else None,
        missing_fx_funds=r['missing_fx_funds']) for r in data]
    return dict(currency='CLP', mode='consolidated', granularity='daily', points=points)
