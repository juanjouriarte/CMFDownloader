"""Panorama-aligned latest, exact-day and explicit month-end administrator snapshots."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
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
def load_analysis(currency, kind, period, as_of, mode, category, consolidated, bucket, aum_source="nav"):
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
    if aum_source != 'nav' and kind != 'fm':
        from .administrator_equity import report_data
        from src.api.fx import month_ends
        _,stored_reports=report_data(date(2020,1,1),date.today(),kind)
        report_dates=[r['periodo'] for r in stored_reports if native is None or r['currency']==native]
        if report_dates:
            latest=max([*report_dates,*([latest] if latest else [])])
            periods=sorted(set(periods)|{m for m,_ in month_ends(min(report_dates),latest)},reverse=True)
    latest_snapshot = None
    if mode == 'latest':
        from .administrator_latest import snapshot
        latest_snapshot = snapshot(kind,aum_source)
        requested = latest_snapshot['date']
        selected = requested.replace(day=1)
        raw = monthly_data(native,kind,max(date(2020,1,1),month_shift(selected,-12)),
                           month_shift(selected,-1),active_only=True) if aum_source != 'quarterly_report' else []
        periods = sorted(set(periods)|{selected},reverse=True)
    elif mode == 'day':
        selected, requested = as_of.replace(day=1), as_of
        raw = daily_data(native,kind,comparison_dates(as_of)) if aum_source != "quarterly_report" else []
    else:
        selected = period or (periods[0] if periods else date.today().replace(day=1))
        requested = min(selected.replace(day=monthrange(selected.year,selected.month)[1]),date.today())
        raw = monthly_data(native,kind,max(date(2020,1,1),month_shift(selected,-12)),selected,active_only=True) if aum_source != "quarterly_report" else []
        raw = [r for r in raw if r['latest_data_date'] <= requested]
    if aum_source != 'nav' or mode == 'latest':
        from .administrator_equity import report_data, analyze
        from src.api.fx import month_ends
        start=max(date(2020,1,1),month_shift(selected,-12))
        candidates,reports=report_data(start,requested,kind)
        cutoffs=comparison_dates(as_of) if mode=='day' else dict(month_ends(start,requested))
        book=Rates.load(start-timedelta(days=185),requested) if consolidated else None
        if latest_snapshot:
            latest_snapshot['excluded'] = [r for r in latest_snapshot['excluded']
                if (not category or r['category']==category) and (native is None or r.get('currency') in (None,native))]
        result=analyze(raw,candidates,reports,cutoffs,selected,currency,kind,category,mode,requested,aum_source,book,
                       {selected:latest_snapshot} if latest_snapshot else None)
        if latest_snapshot:
            result.update(reference_dates=latest_snapshot['reference_dates'], reference_coverage_ratio=0.9,
                          reference_window_days=7,categories=sorted({r['category'] for r in latest_snapshot['rows']
                          if native is None or r['currency']==native}))
        return dict(result,periods=periods,latest_date=max(latest or requested,requested),min_date=date(2020,1,1))
    book = Rates.load(min((r['latest_data_date'] for r in raw),default=None),requested) if consolidated else None
    result = build_dated_analysis(raw,selected,currency,kind,category,mode,requested,book)
    return dict(result,periods=periods,latest_date=latest,min_date=date(2020,1,1))
