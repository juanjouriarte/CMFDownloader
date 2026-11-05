"""Exact-day and latest-reported-month-end administrator snapshots."""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from functools import lru_cache

from src.fx import Rates
from src.api.industry import FM_CURRENCY, FI_CURRENCY
from .administrators import build_analysis, month_shift
from .administrator_fx import monthly_samples


def comparison_dates(day):
    return {month_shift(day, offset): month_shift(day, offset).replace(
        day=min(day.day, monthrange(month_shift(day, offset).year, month_shift(day, offset).month)[1]))
        for offset in range(-12, 1) if month_shift(day, offset) >= date(2020, 1, 1)}


def daily_data(currency, kind, dates):
    from .router import rows
    parts = []
    for k, table, fund_table, alias, name, admin, active, expression in [
        ('fm','cartola_diaria','fondo_mutuo','cd','nombre_fondo','razon_social_administradora','f.fecha_termino_operaciones IS NULL',FM_CURRENCY),
        ('fi','valores_cuota_fi','fondos_inversion','v','razon_social','administrador','f.vigente IS TRUE',FI_CURRENCY),
    ]:
        if kind not in ('all', k):
            continue
        parts.append(f"""SELECT '{k}' kind,{alias}.run_fondo,{alias}.fecha latest_data_date,
            date_trunc('month',{alias}.fecha)::date AS month,{expression} currency,
            f.{name} fund_name,COALESCE(f.{admin},'Sin administradora') admin,
            COALESCE(c.nombre_cat,'Sin clasificación') category,SUM({alias}.patrimonio_neto) aum
            FROM {table} {alias} JOIN {fund_table} f USING(run_fondo)
            LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_{k}_effective
                WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
            WHERE {alias}.fecha=ANY(:dates) AND {active}
                AND (:currency IS NULL OR {expression}=:currency)
                AND {alias}.patrimonio_neto IS NOT NULL AND {alias}.patrimonio_neto>=0
                AND {alias}.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')
            GROUP BY {alias}.run_fondo,{alias}.fecha,{expression},f.{name},f.{admin},c.nombre_cat""")
    return rows(' UNION ALL '.join(parts), dict(currency=currency, dates=list(dates.values())))


def build_dated_analysis(raw, selected, currency, kind, category, mode, requested, rates=None):
    categories = sorted({r['category'] for r in raw if r['month'] == selected})
    chosen = [r for r in raw if not category or r['category'] == category]
    eligible = defaultdict(set)
    references = {}
    for r in chosen:
        month = r['month']
        eligible[month].add((r['kind'],r['run_fondo']))
        references[month] = max(references.get(month,r['latest_data_date']), r['latest_data_date'])
    # One date for every AGF; month-end means the latest observation, not the most-covered earlier day.
    sample = [r for r in chosen if r['latest_data_date'] == references[r['month']]]
    if rates is not None:
        sample, fx_coverage = monthly_samples(sample, rates)
    else:
        fx_coverage = {}
    result = build_analysis(sample, selected, 'CLP' if rates is not None else currency, kind, category)
    for snap in result['history']:
        month = snap['month']
        snap.update(fx_coverage.get(month, dict(missing_fx_funds=0, fx=[])))
        if mode == 'month_end':
            snap['eligible_funds'] = len(eligible[month])
            snap['coverage_pct'] = snap['reported_funds']/len(eligible[month])*100 if eligible[month] else None
        else:
            # An exact-day extract cannot establish how many funds should have filed.
            snap['eligible_funds'] = None
            snap['coverage_pct'] = None
    for key in ('date','eligible_funds','reported_funds','coverage_pct','missing_fx_funds','fx'):
        result[key] = result['history'][-1][key]
    if result['missing_fx_funds']:
        result['funds'] = []
    result.update(previous_date=result['history'][-2]['date'], year_date=result['history'][0]['date'],
        mode='consolidated' if rates is not None else 'native', date_mode=mode, as_of=requested,
        categories=categories, max_fx_age_days=7)
    return result


@lru_cache(maxsize=32)
def load_analysis(currency, kind, period, as_of, mode, category, consolidated, bucket):
    from .router import monthly_data, rows
    native = None if consolidated else currency
    tables = [f'mv_industry_monthly_{k}' for k in ('fm','fi') if kind in ('all',k)]
    calendar = rows('SELECT month,MAX(latest_data_date) latest_date FROM ('+' UNION ALL '.join(
        f"SELECT month,latest_data_date FROM {t} WHERE (:currency IS NULL OR currency=:currency) "
        "AND aum IS NOT NULL AND aum>=0 AND aum::text NOT IN ('NaN','Infinity','-Infinity') "
        "AND month>='2020-01-01' AND latest_data_date<=CURRENT_DATE" for t in tables
    )+') source GROUP BY month ORDER BY month DESC', dict(currency=native))
    periods = [r['month'] for r in calendar]
    latest = max((r['latest_date'] for r in calendar), default=None)
    if mode == 'day':
        selected, requested = as_of.replace(day=1), as_of
        raw = daily_data(native,kind,comparison_dates(as_of))
    else:
        selected = period or (periods[0] if periods else date.today().replace(day=1))
        requested = min(selected.replace(day=monthrange(selected.year,selected.month)[1]),date.today())
        raw = monthly_data(native,kind,max(date(2020,1,1),month_shift(selected,-12)),selected,active_only=True)
        raw = [r for r in raw if r['latest_data_date'] <= requested]
    book = Rates.load(min((r['latest_data_date'] for r in raw),default=None),requested) if consolidated else None
    result = build_dated_analysis(raw,selected,currency,kind,category,mode,requested,book)
    return dict(result,periods=periods,latest_date=latest,min_date=date(2020,1,1))
