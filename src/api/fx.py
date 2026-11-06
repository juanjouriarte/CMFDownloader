"""Stored FX and Panorama snapshots/history in native currencies or consolidated CLP."""
from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from src.etl.fx.catalog import SERIES
from src.fx import Rates, MAX_AGE_DAYS
from .deps import CacheHook
from .industry import _rows, _snapshot_cte, _filters, _reference, _monthly_history_is_current, FM_CURRENCY, FI_CURRENCY, FundType
from .reported_equity import supplement, MAX_REPORT_AGE_DAYS

router = APIRouter(tags=['FX'])
Currency = Annotated[str | None, Query(pattern=r'^(?:[A-Z]{3}|UF)$')]


def native_aum(rows):
    """Rows must already be restricted to one currency; no FX dependency."""
    return [dict(r, native_aum=r['aum'], fx_missing=False, fx_date=None) for r in rows]


def matches(row, admin=None, category=None, category_type=None, category_group=None):
    return ((not admin or admin.casefold() in (row.get('administrator') or '').casefold())
            and (category is None or (row.get('category_name') or '') == category)
            and (category_type is None or (row.get('category_type') or '') == category_type)
            and (category_group is None or (row.get('category_group') or '') == category_group))


@router.get('/fx/coverage')
def coverage(_: CacheHook):
    rows = _rows('''SELECT s.*,min(o.date) FILTER(WHERE o.status='OK') first_date,
        max(o.date) FILTER(WHERE o.status='OK') last_date,
        count(o.date) FILTER(WHERE o.status='OK') observations
        FROM fx_series s LEFT JOIN fx_observations o USING(series_code)
        GROUP BY s.currency ORDER BY s.currency''')
    stored = {r['currency']:r for r in rows}
    return dict(source='Banco Central de Chile',max_age_days=MAX_AGE_DAYS,
        series=[dict(spec,**{k:v for k,v in stored.get(spec['currency'],{}).items() if k not in spec}) for spec in SERIES])


@router.get('/fx/rates')
def rates(_: CacheHook, currency: str = Query(pattern=r'^(?:[A-Z]{3}|UF)$'),
          from_date: date = date(2020,1,1), to_date: date | None = None):
    return _rows('''SELECT * FROM fx_daily WHERE currency=:currency
        AND date BETWEEN :start AND :end ORDER BY date''',
        dict(currency=currency,start=from_date,end=to_date or date.today()))


def convert_aum(rows, rates):
    converted, details = [], {}
    for original in rows:
        row = dict(original)
        rate = rates.get(row['currency'],row['data_date'])
        row['native_aum'] = row['aum']
        row['aum'] = row['aum']*rate['clp_per_unit'] if rate else None
        row['fx_missing'] = rate is None
        row['fx_date'] = rate['date'] if rate else None
        converted.append(row)
        source = row.get('aum_source','nav')
        key = (row['currency'],row['data_date'],source)
        item = details.setdefault(key,dict(currency=row['currency'],nav_date=row['data_date'],
            aum_source=source,rate_date=rate['date'] if rate else None,rate=rate['clp_per_unit'] if rate else None,
            series_code=rate['series_code'] if rate else None,funds=set(),native_aum=Decimal(0),aum_clp=Decimal(0)))
        item['funds'].add((row['fund_type'],row['run_fondo']))
        item['native_aum'] += row['native_aum']
        if row['aum'] is not None:item['aum_clp'] += row['aum']
    for item in details.values():
        item['funds'] = len(item['funds'])
        if item['rate'] is None:item['aum_clp'] = None
    return converted,sorted(details.values(),key=lambda r:(r['currency'] or '',r['nav_date']))


def aggregate(rows):
    missing = { (r['fund_type'],r['run_fondo']) for r in rows if r['fx_missing'] }
    covered = sum((r['aum'] for r in rows if r['aum'] is not None),Decimal(0))
    reports=[r for r in rows if r.get('aum_source')=='quarterly_report']
    nav=[r for r in rows if r.get('aum_source','nav')=='nav']
    def subtotal(items):
        return None if any(r['fx_missing'] for r in items) else sum((r['aum'] for r in items),Decimal(0))
    return dict(aum=None if missing or not rows else covered,covered_aum=covered,
        nav_aum=subtotal(nav),reported_aum=subtotal(reports),
        nav_funds=len({(r['fund_type'],r['run_fondo']) for r in nav}),
        reported_funds=len({(r['fund_type'],r['run_fondo']) for r in reports}),
        nav_date=max((r['data_date'] for r in nav),default=None),
        first_report_date=min((r['data_date'] for r in reports),default=None),
        last_report_date=max((r['data_date'] for r in reports),default=None),
        funds=len({(r['fund_type'],r['run_fondo']) for r in rows}),missing_fx_funds=len(missing),
        first_fx_date=min((r['fx_date'] for r in rows if r.get('fx_date')),default=None),
        last_fx_date=max((r['fx_date'] for r in rows if r.get('fx_date')),default=None),
        date=max((r['data_date'] for r in rows),default=None))


def grouped(rows, keys):
    groups = defaultdict(list)
    for row in rows:groups[tuple(row[k] for k in keys)].append(row)
    return sorted([dict(zip(keys,key),**aggregate(items)) for key,items in groups.items()],
        key=lambda r:r['covered_aum'],reverse=True)


def flow_total(points, rates):
    amount = Decimal(0); missing = 0
    for row in points:
        rate = rates.get(row['currency'],row['date']) if rates is not None else {'clp_per_unit': Decimal(1)}
        if row['amount'] is None or not rate:missing += 1
        else:amount += row['amount']*rate['clp_per_unit']
    return dict(amount=amount if points and not missing else None,
                covered_amount=amount,missing_points=missing,points=len(points))


@router.get('/industry/consolidated')
@router.get('/industry/panorama')
def consolidated(_: CacheHook, fund_type: FundType = 'all', admin: str | None = None,
                 nombre_cat: str | None = None, include_reported_equity: bool = True,
                 aum_source: Literal['all','nav','quarterly_report'] | None = None,
                 currency: Currency = None, category_type: str | None = None,
                 category_group: str | None = None):
    selected_source = aum_source or ('all' if include_reported_equity else 'nav')
    # Apply currency only AFTER EEFF enrichment: FI identity often has no currency.
    # Keep options from the entire selected source/currency universe.
    params = dict(fund_type=fund_type,currency=None,admin=None,nombre_cat=None,vigente=True)
    snapshot = _rows(f'{_snapshot_cte(include_returns=False,include_ytd=False)} SELECT * FROM snapshot {_filters(params)}',params)
    rows = [dict(r,aum_source='nav') for r in snapshot if r['aum'] is not None] if selected_source!='quarterly_report' else []
    end = max((r['data_date'] for r in rows),default=None)
    # A filtered category can have quarterly-only funds and no daily NAV at all.
    cutoff = _rows(f'SELECT ({_reference("valores_cuota_fi")}) cutoff')[0]['cutoff']
    added, excluded = supplement(snapshot,cutoff) if selected_source!='nav' else ([],[])
    rows += added
    currencies = sorted({r['currency'] for r in rows if r['currency']})
    rows = [r for r in rows if currency is None or r['currency']==currency]
    options = [dict(kind=r['fund_type'],run=r['run_fondo'],name=r['name'],
                    admin=r['administrator'],category=r['category_name'] or '',
                    category_type=r['category_type'],category_group=r['category_group'],
                    currency=r['currency'],aum=r['aum'],data_date=r['data_date']) for r in rows]
    rows = [r for r in rows if matches(r,admin,nombre_cat,category_type,category_group)]
    candidates = {r['run_fondo']:r for r in snapshot if r['fund_type']=='fi'}
    excluded = [r for r in excluded if matches(candidates[r['run_fondo']],admin,nombre_cat,category_type,category_group)
                and (currency is None or r.get('currency') in (None,currency))]
    end = max((r['data_date'] for r in rows),default=end)
    start = min((r['data_date'].replace(day=1) for r in rows),default=None)
    ratebook = Rates.load(start,end) if currency is None else None
    converted, detail = convert_aum(rows,ratebook) if currency is None else (native_aum(rows),[])
    total = aggregate(converted)
    admins = grouped(converted,('administrator',))
    for item in admins:
        item['share'] = item['aum']*100/total['aum'] if total['aum'] and item['aum'] is not None else None
    # Flows are translated on each reporting date, including signed internal-flow corrections.
    fm = [r for r in rows if r['fund_type']=='fm']
    flow_points = []
    if fm:
        flow_cutoff = max(r['data_date'] for r in fm)
        points = _rows(f'''WITH points AS (
            SELECT cd.run_fondo,cd.fecha date,{FM_CURRENCY} currency,
                   SUM(cd.monto_aportado-cd.monto_rescatado) amount
            FROM cartola_diaria cd JOIN fondo_mutuo f USING(run_fondo)
            WHERE cd.fecha BETWEEN :start AND :end AND cd.run_fondo=ANY(:runs)
            GROUP BY cd.run_fondo,cd.fecha,{FM_CURRENCY}
            UNION ALL
            SELECT target_run_fondo,event_date,currency,-amount FROM fm_flow_adjustments
            WHERE event_date BETWEEN :start AND :end AND target_run_fondo=ANY(:runs)
              AND status IN ('auto_confirmed','confirmed')) SELECT * FROM points''',
            dict(start=flow_cutoff.replace(day=1),end=flow_cutoff,runs=list({r['run_fondo'] for r in fm})))
        eligible = {(r['run_fondo'],r['currency']) for r in fm}
        flow_points = [p for p in points if (p['run_fondo'],p['currency']) in eligible]
    return dict(currency=currency or 'CLP',mode='native' if currency else 'consolidated',
        source='CMF' if currency else 'CMF / Banco Central de Chile',aum_source=selected_source,
        options=options,currencies=currencies,
        total=total,breakdown=grouped(converted,('fund_type',)),administrators=admins,
        categories=grouped(converted,('fund_type','category_type','category_group','category_name')),
        fx=detail,flow=flow_total(flow_points,ratebook),max_age_days=MAX_AGE_DAYS,
        reported_equity=dict(enabled=selected_source!='nav',as_of=cutoff,max_age_days=MAX_REPORT_AGE_DAYS,
            excluded=excluded,funds=[dict(run_fondo=r['run_fondo'],name=r['name'],
              administrator=r['administrator'],category=r['category_name'],currency=r['currency'],
              native_aum=r['native_aum'],aum=r['aum'],aum_clp=r['aum'] if currency in (None,'CLP') else None,
              date=r['data_date'],source_url=r['source_url'])
              for r in converted if r.get('aum_source')=='quarterly_report']))


@router.get('/industry/consolidated/evolution')
@router.get('/industry/panorama/evolution')
def consolidated_evolution(_: CacheHook, fund_type: FundType = 'all',
    from_date: date = date(2020,1,1), to_date: date | None = None,
    admin: str | None = None, nombre_cat: str | None = None,
    currency: Currency = None, category_type: str | None = None, category_group: str | None = None):
    # Exact underlying valuation dates are required for FX. Never multiply a monthly
    # aggregate by one rate when its funds report on different dates.
    params = dict(start=from_date.replace(day=1),end=min(to_date or date.today(),date.today()),
        fund_type=fund_type,admin=f'%{admin}%' if admin else None,category=nombre_cat,
        category_type=category_type,category_group=category_group,currency=currency)
    use_monthly = (to_date is None or to_date >= date.today()) and _monthly_history_is_current(fund_type)
    parts = []
    for kind,table,alias,currency_sql,name in [('fm','cartola_diaria','cd',FM_CURRENCY,'fondo_mutuo'),
                                         ('fi','valores_cuota_fi','v',FI_CURRENCY,'fondos_inversion')]:
        admin_col = 'razon_social_administradora' if kind=='fm' else 'administrador'
        if use_monthly:
            parts.append(f'''SELECT '{kind}' fund_type,x.run_fondo,x.currency,
                x.latest_data_date data_date,x.aum,x.month
                FROM mv_industry_monthly_{kind} x JOIN {name} f USING(run_fondo)
                LEFT JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_{kind}_effective c
                  WHERE c.run_fondo=f.run_fondo ORDER BY periodo DESC LIMIT 1) c ON true
                WHERE :fund_type IN ('all','{kind}') AND x.month BETWEEN :start AND :end
                  AND (:admin IS NULL OR f.{admin_col} ILIKE :admin)
                  AND (:category IS NULL OR COALESCE(c.nombre_cat,'')=:category)
                  AND (:category_type IS NULL OR COALESCE(c.tipo,'')=:category_type)
                  AND (:category_group IS NULL OR COALESCE(c.grupo,'')=:category_group)''')
            continue
        parts.append(f'''SELECT '{kind}' fund_type,{alias}.run_fondo,{currency_sql} currency,
            {alias}.fecha data_date,SUM({alias}.patrimonio_neto) aum
            FROM {table} {alias} JOIN {name} f USING(run_fondo)
            LEFT JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_{kind}_effective c
              WHERE c.run_fondo=f.run_fondo ORDER BY periodo DESC LIMIT 1) c ON true
            WHERE :fund_type IN ('all','{kind}') AND {alias}.fecha BETWEEN :start AND :end
              AND (:admin IS NULL OR f.{admin_col} ILIKE :admin)
              AND (:category IS NULL OR COALESCE(c.nombre_cat,'')=:category)
              AND (:category_type IS NULL OR COALESCE(c.tipo,'')=:category_type)
              AND (:category_group IS NULL OR COALESCE(c.grupo,'')=:category_group)
            GROUP BY {alias}.run_fondo,{currency_sql},{alias}.fecha''')
    sql = ' UNION ALL '.join(parts) if use_monthly else '''WITH daily AS ('''+ ' UNION ALL '.join(parts)+'''),
        monthly AS (SELECT DISTINCT ON(fund_type,run_fondo,currency,date_trunc('month',data_date))
          *,date_trunc('month',data_date)::date AS month FROM daily
          ORDER BY fund_type,run_fondo,currency,date_trunc('month',data_date),data_date DESC)
        SELECT * FROM monthly'''
    # A fund that changes reporting currency during a month must not be counted
    # once in the old currency and again in the new one. Keep its closing date.
    rows = _rows('''WITH source AS ('''+sql+'''), aligned AS (
        SELECT *,MAX(data_date) OVER(PARTITION BY fund_type,run_fondo,month) close_date
        FROM source) SELECT * FROM aligned WHERE data_date=close_date AND aum IS NOT NULL
        AND (:currency IS NULL OR currency=:currency)''',params)
    if currency is None:
        ratebook = Rates.load(min((r['data_date'] for r in rows),default=None),params['end'])
        converted,_ = convert_aum(rows,ratebook)
    else:
        converted = native_aum(rows)
    result = grouped(converted,('month','fund_type'))
    return sorted(result,key=lambda r:(r['month'],r['fund_type']))


@router.get('/industry/panorama/reported-evolution')
def reported_evolution(_: CacheHook, fund_type: FundType = 'all',
    from_date: date = date(2020,1,1), to_date: date | None = None,
    admin: str | None = None, nombre_cat: str | None = None,
    currency: Currency = None, category_type: str | None = None, category_group: str | None = None,
    basis: Literal['reported','latest'] = 'reported'):
    """Actual closes or bounded latest-report monthly valuations; never monthly NAV."""
    if fund_type=='fm':return []
    from src.etl.investmentFunds.reported_equity import ELIGIBLE_SQL
    cutoff = _rows(f'SELECT ({_reference("valores_cuota_fi")}) cutoff')[0]['cutoff']
    if cutoff is None:return []
    rows = _rows('''WITH eligible AS ('''+ELIGIBLE_SQL+''')
        SELECT 'fi' fund_type,r.run_fondo,r.currency,r.equity aum,r.periodo data_date,
          date_trunc('month',r.periodo)::date AS month,'quarterly_report' aum_source,
          f.administrador administrator,c.tipo category_type,c.grupo category_group,c.nombre_cat category_name
        FROM fi_reported_equity r JOIN eligible e USING(run_fondo)
        JOIN fondos_inversion f USING(run_fondo)
        LEFT JOIN LATERAL (SELECT tipo,grupo,nombre_cat FROM categoria_fi_effective c
          WHERE c.run_fondo=r.run_fondo ORDER BY periodo DESC LIMIT 1) c ON true
        WHERE r.periodo BETWEEN :start AND :end AND (:latest OR r.equity>=0)
          AND (:latest OR r.equity::text NOT IN ('NaN','Infinity','-Infinity'))
          AND (:currency IS NULL OR r.currency=:currency)''',
        dict(today=cutoff,start=from_date.replace(day=1)-timedelta(days=MAX_REPORT_AGE_DAYS) if basis=='latest' else from_date,
             end=min(to_date or cutoff,cutoff),currency=None if basis=='latest' else currency,latest=basis=='latest'))
    rows = [r for r in rows if matches(r,admin,nombre_cat,category_type,category_group)]
    end=min(to_date or cutoff,cutoff)
    if basis=='latest':
        rows=latest_report_months(rows,from_date,end,currency)
    if currency is None:
        converted,_ = convert_aum(rows,Rates.load(min((r['data_date'] for r in rows),default=None),cutoff))
    else:
        converted = native_aum(rows)
    result=grouped(converted,('month','fund_type'))
    if basis=='latest':
        by_month={r['month']:r for r in result}
        result=[]
        for month,as_of in month_ends(from_date,end):
            point=by_month.get(month,dict(month=month,fund_type='fi',**aggregate([])))
            point.update(as_of=as_of,basis='latest',max_report_age_days=MAX_REPORT_AGE_DAYS,
                         carried_funds=len({r['run_fondo'] for r in rows if r['month']==month and r['data_date']<month}))
            result.append(point)
    return sorted(result,key=lambda r:r['month'])


def month_ends(start, end):
    month=start.replace(day=1)
    while month<=end:
        last=month.replace(day=monthrange(month.year,month.month)[1])
        yield month,min(last,end)
        month=last+timedelta(days=1)


def latest_report_months(rows, start, end, currency=None):
    """Select per fund BEFORE currency/value filtering, so no stale fallback after
    a currency change or a negative report. Retain actual valuation/FX dates.
    The historical cohort is today's eligible FI; publication dates are unknown.
    """
    ordered=sorted(rows,key=lambda r:r['data_date'])
    latest={};index=0;result=[]
    for month,as_of in month_ends(start,end):
        while index<len(ordered) and ordered[index]['data_date']<=as_of:
            row=ordered[index];latest[row['run_fondo']]=row;index+=1
        for row in latest.values():
            value=row['aum']
            if (row['data_date']<as_of-timedelta(days=MAX_REPORT_AGE_DAYS)
                or value is None or not value.is_finite() or value<0
                or (currency is not None and row['currency']!=currency)):
                continue
            result.append(dict(row,month=month,as_of=as_of))
    return result
