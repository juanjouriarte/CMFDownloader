"""Administrator snapshots combining NAV with bounded quarterly FI net equity."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

from src.api.reported_equity import select_reports, MAX_REPORT_AGE_DAYS
from src.etl.investmentFunds.reported_equity import ELIGIBLE_SQL
from src.fx import Rates


def report_data(start, end, kind='all'):
    from .router import rows
    if kind == 'fm':
        return [], []
    candidates = rows('''WITH eligible AS ('''+ELIGIBLE_SQL+''')
        SELECT 'fi' kind,f.run_fondo,f.razon_social name,
            COALESCE(f.administrador,'Sin administradora') admin,
            COALESCE(c.nombre_cat,'Sin clasificación') category
        FROM eligible e JOIN fondos_inversion f USING(run_fondo)
        LEFT JOIN LATERAL (SELECT nombre_cat FROM categoria_fi_effective c
            WHERE c.run_fondo=f.run_fondo AND periodo<=CURRENT_DATE
            ORDER BY periodo DESC LIMIT 1) c ON true''', dict(today=date.today()))
    reports = rows('''SELECT run_fondo,periodo,currency,equity,source_url
        FROM fi_reported_equity WHERE run_fondo=ANY(:runs)
        AND periodo BETWEEN :start AND :end ORDER BY periodo''',
        dict(runs=[r['run_fondo'] for r in candidates],
             start=start-timedelta(days=MAX_REPORT_AGE_DAYS),end=end)) if candidates else []
    return candidates, reports


def mixed_samples(raw, candidates, reports, cutoffs, source, currency, category=None, rates=None, snapshots=None):
    """latest_data_date is the analysis cut; valuation_date is the original NAV/EEFF date.
    Conversion always uses valuation_date. Never replace an invalid latest report.
    """
    by_month = defaultdict(list)
    for row in raw:
        if not category or row['category'] == category:
            by_month[row['month']].append(row)
    candidates = [r for r in candidates if not category or r['category'] == category]
    output, coverage = [], {}
    for month, cutoff in sorted(cutoffs.items()):
        available = [r for r in by_month[month] if r['latest_data_date'] <= cutoff]
        nav_date = max((r['latest_data_date'] for r in available), default=None)
        reference = nav_date if source != 'quarterly_report' and nav_date else cutoff
        nav = [dict(r,valuation_date=r['latest_data_date'],aum_source='nav')
               for r in available if r['latest_data_date'] == nav_date] if source != 'quarterly_report' else []
        latest = {}
        for report in sorted(reports,key=lambda r:r['periodo']):
            if report['periodo'] <= reference:
                latest[report['run_fondo']] = report
        selected, excluded = select_reports(candidates, latest, reference) if source != 'nav' else ([], [])
        nav_keys = {(r['kind'],r['run_fondo']) for r in nav}
        selected = [r for r in selected if (currency is None or r['currency']==currency)
                    and ('fi',r['run_fondo']) not in nav_keys]
        admins={r['run_fondo']:r['admin'] for r in candidates}
        excluded = [dict(r,admin=admins[r['run_fondo']]) for r in excluded if currency is None or r.get('currency') in (None,currency)]
        sample = nav + [dict(r,month=month,fund_name=r['name'],valuation_date=r['data_date']) for r in selected]
        if snapshots and month in snapshots:
            # Latest mode supplies the exact Panorama universe and actual valuation
            # dates. Do not discard a fund type if FM/FI reference dates differ.
            snapshot = snapshots[month]
            reference = snapshot['date']
            sample = [r for r in snapshot['rows'] if (currency is None or r['currency']==currency)
                      and (not category or r['category']==category)]
            nav = [r for r in sample if r['aum_source']=='nav']
            nav_date = max((r['valuation_date'] for r in nav),default=None)
            excluded = snapshot['excluded']
        funds, fx, missing = {}, {}, set()
        for row in sample:
            key = (row['kind'],row['run_fondo'])
            actual = row['valuation_date']
            rate = rates.get(row['currency'],actual) if rates is not None else None
            value = Decimal(str(row['aum']))
            fund = funds.setdefault(key,dict(row,latest_data_date=reference,aum=Decimal(0),native_currencies=set()))
            fund['native_currencies'].add(row['currency'])
            if rates is None:
                fund['aum'] += value
            elif rate:
                fund['aum'] += value*rate['clp_per_unit']
            else:
                missing.add(key)
            if rates is not None:
                detail = fx.setdefault((row['currency'],actual,row['aum_source']),dict(currency=row['currency'],
                    nav_date=actual,aum_source=row['aum_source'],rate_date=rate['date'] if rate else None,
                    rate=rate['clp_per_unit'] if rate else None,funds=0))
                detail['funds'] += 1
        nav_funds = sum(r['aum_source']=='nav' for r in funds.values())
        report_funds = sum(r['aum_source']=='quarterly_report' for r in funds.values())
        report_dates = [r['valuation_date'] for r in funds.values() if r['aum_source']=='quarterly_report']
        coverage[month] = dict(date=reference if funds else None,nav_date=nav_date if nav else None,
            nav_funds=nav_funds,eeff_funds=report_funds,reported_funds=len(funds),eligible_funds=None,coverage_pct=None,
            nav_aum=None if missing else sum(float(r['aum']) for r in funds.values() if r['aum_source']=='nav'),
            eeff_aum=None if missing else sum(float(r['aum']) for r in funds.values() if r['aum_source']=='quarterly_report'),
            first_report_date=min(report_dates,default=None),last_report_date=max(report_dates,default=None),
            missing_fx_funds=len(missing),fx=list(fx.values()),excluded_reports=excluded)
        if not missing:
            for fund in funds.values():
                currencies = fund.pop('native_currencies')
                fund['native_currency'] = next(iter(currencies)) if len(currencies)==1 else None
                output.append(fund)
    return output, coverage


def analyze(raw, candidates, reports, cutoffs, selected, currency, kind, category, mode, requested, source, rates=None, snapshots=None):
    from .administrators import build_analysis
    sample, coverage = mixed_samples(raw,candidates,reports,cutoffs,source,None if rates is not None else currency,category,rates,snapshots)
    result = build_analysis(sample,selected,'CLP' if rates is not None else currency,kind,category)
    for snap in result['history']:
        snap.update(coverage.get(snap['month'],{}))
    current = coverage[selected]
    result.update(current)
    if current['missing_fx_funds']:
        result['funds'] = []
    result.update(aum_source=source,max_report_age_days=MAX_REPORT_AGE_DAYS,max_fx_age_days=7,
        mode='consolidated' if rates is not None else 'native',date_mode=mode,as_of=requested,
        previous_date=result['history'][-2]['date'],year_date=result['history'][0]['date'],
        categories=sorted({r['category'] for r in raw if r['month']==selected}|
                          {r['category'] for r in candidates}) if source=='all' else
                   sorted({r['category'] for r in candidates}))
    return result


@lru_cache(maxsize=32)
def load_history(currency,kind,admin,start,end,category,metric,consolidated,source,bucket):
    from .administrator_dates import daily_data
    from .router import monthly_data, market_snapshots
    from src.api.fx import month_ends
    native = None if consolidated else currency
    cutoffs = dict(month_ends(start,end))
    # Monthly closes plus an exact endpoint; no later data enters a custom range.
    raw = monthly_data(native,kind,start.replace(day=1),end.replace(day=1)-timedelta(days=1),active_only=True) if source!='quarterly_report' else []
    if source!='quarterly_report':
        raw += daily_data(native,kind,{end.replace(day=1):end})
    raw = [r for r in raw if r['latest_data_date']>=start]
    candidates,reports = report_data(start.replace(day=1),end,kind)
    rates = Rates.load(start-timedelta(days=MAX_REPORT_AGE_DAYS),end) if consolidated else None
    sample,coverage = mixed_samples(raw,candidates,reports,cutoffs,source,native,category,rates)
    snapshots = {s['month']:s for s in market_snapshots(sample)}
    points=[]
    for month,cut in cutoffs.items():
        meta=coverage[month];snap=snapshots.get(month,{})
        own=next((a for a in snap.get('administrators',[]) if a['admin']==admin),None)
        own_rows=[r for r in sample if r['month']==month and r['admin']==admin]
        dates=[r['valuation_date'] for r in own_rows if r['aum_source']=='quarterly_report']
        points.append(dict(date=meta['date'] or cut,aum=own['aum'] if own else None,
            share=own['share'] if own and metric=='share' else None,funds=own['funds'] if own else 0,
            market_funds=meta['reported_funds'],missing_fx_funds=meta['missing_fx_funds'],
            eeff_funds=len(dates),first_report_date=min(dates,default=None),last_report_date=max(dates,default=None)))
    return dict(currency='CLP' if consolidated else currency,granularity='monthly',aum_source=source,points=points)
