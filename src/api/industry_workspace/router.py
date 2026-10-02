"""Read-only industry workspace queries, isolated under /industry-workspace."""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from math import isfinite
from typing import Literal
from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import text
from src.db.engine import SessionLocal

router = APIRouter(prefix="/industry-workspace", tags=["Industry workspace"])

def rows(sql, params=None):
    with SessionLocal() as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        s.execute(text("SET LOCAL statement_timeout = '20s'"))
        return [dict(r) for r in s.execute(text(sql), params or {}).mappings()]

@router.get('/administrators')
def administrators(currency: str = Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'),
                   fund_type: Literal['all', 'fm', 'fi'] = 'all',
                   admin: str | None = None, category: str | None = None):
    # Same common-date, active NAV universe as Panorama, without its top-ten cap.
    from src.api.industry import _snapshot_cte, _filters
    params = dict(currency=currency, fund_type=fund_type, admin=f'%{admin}%' if admin else None,
                  nombre_cat=category, vigente=True, rescatable=None)
    return rows(f"""{_snapshot_cte(include_returns=False, include_ytd=False)},
        selected AS (SELECT * FROM snapshot {_filters(params)} AND aum IS NOT NULL),
        grouped AS (
            SELECT COALESCE(administrator, 'Sin administradora informada') AS administrador,
                   SUM(aum) AS aum, COUNT(DISTINCT (fund_type, run_fondo)) AS active_funds
            FROM selected GROUP BY administrator
        )
        SELECT *, COALESCE(ROUND(aum * 100 / NULLIF(SUM(aum) OVER (), 0), 2), 0) AS market_share_pct,
               NULL::numeric AS nnm_ytd
        FROM grouped ORDER BY aum DESC NULLS LAST, administrador""", params)


@router.get('/funds')
def funds():
    parts=[]
    for kind,table,name in [('fm','fondo_mutuo','nombre_fondo'),('fi','fondos_inversion','razon_social')]:
        parts.append(f"""SELECT '{kind}' kind, f.run_fondo run, f.{name} name, f.{'razon_social_administradora' if kind=='fm' else 'administrador'} admin,
        c.nombre_cat category, m.currency, m.aum, m.latest_data_date data_date
        FROM (SELECT DISTINCT ON (run_fondo,currency) *, MAX(latest_data_date) OVER(PARTITION BY run_fondo) latest_fund_date FROM mv_industry_monthly_{kind}
        WHERE aum IS NOT NULL ORDER BY run_fondo,currency,month DESC) m
        JOIN {table} f USING(run_fondo) LEFT JOIN (SELECT DISTINCT ON(run_fondo) * FROM categoria_{kind}_effective WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
        WHERE m.latest_data_date=m.latest_fund_date AND {"f.fecha_termino_operaciones IS NULL" if kind=='fm' else 'f.vigente IS TRUE'}""")
    return rows(' UNION ALL '.join(parts)+' ORDER BY currency, aum DESC NULLS LAST')

# Identity is the reported identifier + instrument type + issuer identifier/name.
SOURCES=[('fm','cartera_naci','nemotecnico','rut_emisor',"NULL::text",'porcentaje_activos_fondo'),
('fm','cartera_extr','nemotecnico',"NULL::text",'nombre_emisor','porcentaje_activos_fondo'),
('fi','cartera_fi_nac','nemotecnico','rut_emisor',"NULL::text",'pct_activo_fondo'),
('fi','cartera_fi_ext','nemo_isin',"NULL::text",'nombre_emisor','pct_activo_fondo')]

def positions_sql(run=None,kind=None,period=None):
    parts=[]
    for k,t,n,r,issuer,w in SOURCES:
        if kind and kind!=k: continue
        weight=f"CASE WHEN BTRIM(c.{w}::text) ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$' THEN c.{w}::numeric END"
        latest=f"SELECT MAX(periodo) FROM {t} WHERE periodo <= CURRENT_DATE"+(' AND run_fondo=:run' if run else '')
        where='c.periodo=CAST(:period AS date)' if period else f'c.periodo=({latest})'
        if run: where+=' AND c.run_fondo=:run'
        parts.append(f"SELECT '{k}' kind, '{t}' source, c.run_fondo run, c.periodo, {n} identifier, {r} issuer_rut, {issuer} issuer_name, tipo_instrumento instrument_type, {'codigo_pais_emisor' if k=='fm' else 'cod_pais'} country_code, {weight} weight FROM {t} c WHERE {where}")
    return ' UNION ALL '.join(parts)

ENRICH="""SELECT p.*, COALESCE(f.nombre_fondo,fi.razon_social) fund_name,
COALESCE(f.razon_social_administradora,fi.administrador) admin, COALESCE(e.razon_social,p.issuer_name,p.issuer_rut,'Sin emisor identificado') issuer,
COALESCE(rc.name,p.instrument_type) instrument_name, COALESCE(country.name,p.country_code,'Sin país') country FROM p
LEFT JOIN fondo_mutuo f ON p.kind='fm' AND f.run_fondo=p.run
LEFT JOIN fondos_inversion fi ON p.kind='fi' AND fi.run_fondo=p.run
LEFT JOIN emisores e ON p.issuer_rut=e.rut
LEFT JOIN ref_codes rc ON rc.domain='instrument' AND rc.code=p.instrument_type
LEFT JOIN ref_codes country ON country.domain='country' AND country.code=p.country_code"""

@router.get('/portfolio/{kind}/{run}')
def portfolio(kind:Literal['fm','fi'],run:str,period:date|None=None):
    ps=[]
    for k,t,*_ in SOURCES:
        if k==kind: ps.append(f'SELECT DISTINCT periodo FROM {t} WHERE run_fondo=:run AND periodo<=CURRENT_DATE')
    periods=rows(' UNION '.join(ps)+' ORDER BY periodo DESC',{'run':run})
    selected=period or (periods[0]['periodo'] if periods else None)
    data=rows('WITH p AS ('+positions_sql(run,kind,selected)+') '+ENRICH+' ORDER BY weight DESC NULLS LAST',{'run':run,'period':selected}) if selected else []
    return {'periods':[r['periodo'] for r in periods], 'period':selected,'positions':data}

@router.get('/instruments')
def instruments(search:str=Query('',max_length=150),identifier:str|None=None,instrument_type:str|None=None,issuer:str|None=None):
    params={'search':'%'+search+'%','identifier':identifier,'instrument_type':instrument_type,'issuer':issuer}
    sql='WITH p AS ('+positions_sql()+'), enriched AS ('+ENRICH+') '
    if identifier:
        return rows(sql+"SELECT * FROM enriched WHERE identifier=:identifier AND instrument_type IS NOT DISTINCT FROM :instrument_type AND issuer=:issuer ORDER BY weight DESC NULLS LAST",params)
    return rows(sql+"""SELECT identifier,instrument_type,issuer, MAX(instrument_name) instrument_name,
    COUNT(DISTINCT (kind,run)) funds, MIN(periodo) oldest, MAX(periodo) newest
    FROM enriched WHERE NULLIF(TRIM(identifier),'') IS NOT NULL AND identifier NOT IN ('NaN','0')
    AND (identifier ILIKE :search OR issuer ILIKE :search)
    GROUP BY identifier,instrument_type,issuer ORDER BY funds DESC,identifier LIMIT 100""",params)

@router.get('/owners')
def owners(period:date|None=None,search:str=Query('',max_length=150),rut:str|None=None,run:str|None=None):
    periods=rows('SELECT DISTINCT periodo FROM aportantes_fi WHERE periodo<=CURRENT_DATE ORDER BY periodo DESC')
    selected=period or (periods[0]['periodo'] if periods else None)
    params={'period':selected,'search':'%'+search+'%','rut':rut,'run':run}
    base="""FROM aportantes_fi a LEFT JOIN fondos_inversion f USING(run_fondo)
    WHERE periodo=:period"""
    if rut or run:
        data=rows("SELECT a.rut,COALESCE(a.nombre_canonical,a.nombre) name,a.run_fondo run,f.razon_social fund_name,f.administrador admin,a.pct_propiedad weight,a.rank,a.periodo "+base+(' AND a.rut=:rut' if rut else ' AND a.run_fondo=:run')+' ORDER BY a.pct_propiedad DESC NULLS LAST',params)
    else:
        data=rows("""SELECT a.rut,MAX(COALESCE(a.nombre_canonical,a.nombre)) name,
        COUNT(DISTINCT run_fondo) funds,COUNT(DISTINCT f.administrador) admins,
        MAX(a.tipo_persona) person_type """+base+""" AND a.rut IS NOT NULL
        AND (COALESCE(a.nombre_canonical,a.nombre) ILIKE :search OR a.rut ILIKE :search)
        GROUP BY a.rut ORDER BY funds DESC,name LIMIT 150""",params)
    return {'periods':[r['periodo'] for r in periods], 'period':selected,'rows':data}

# Advanced analytics remain isolated in the review-only application.


def finite(value):
    if value is None:
        return None
    value = float(value)
    return value if isfinite(value) else None


def capital_changes(current, previous):
    """Quarterly stock changes, never inferred cash calls or currency amounts."""
    result = dict(current)
    for field, key in [('cuotas_emitidas', 'issued'), ('cuotas_pagadas', 'paid'),
                       ('num_cuotas_promesa', 'promised'), ('cuotas_suscritas_no_pagadas', 'unpaid')]:
        end = finite(current.get(field))
        start = finite(previous.get(field)) if previous else None
        valid = end is not None and start is not None and end >= 0 and start >= 0
        change = end - start if valid else None
        result[key + '_delta'] = change
        result[key + '_pct'] = change / start * 100 if valid and start > 0 else None
        result[key + '_previous'] = start if start is not None and start >= 0 else None
    pending = finite(current.get('cuotas_suscritas_no_pagadas'))
    paid = finite(current.get('cuotas_pagadas'))
    result['pending'] = pending if pending is not None and pending >= 0 else None
    result['pending_pct'] = pending / (paid + pending) * 100 if (
        pending is not None and paid is not None and pending >= 0 and paid >= 0 and paid + pending > 0
    ) else None
    result['has_previous'] = previous is not None
    paid_up = (result['paid_delta'] or 0) > 0
    commitments_down = (result['promised_delta'] or 0) < 0 or (result['unpaid_delta'] or 0) < 0
    issued_up = (result['issued_delta'] or 0) > 0
    result['possible_call'] = paid_up and commitments_down
    result['issuance_increase'] = issued_up
    result['signal'] = ('commitment_funding' if paid_up and commitments_down else
                        'paid_increase' if paid_up else 'issuance_increase' if issued_up else 'none')
    result['signal_priority'] = {'commitment_funding': 3, 'paid_increase': 2, 'issuance_increase': 1, 'none': 0}[result['signal']]
    result['confirmed_call'] = False
    return result


@router.get('/capital-activity')
def capital_activity(currency: str = Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'),
                     period: date | None = None, admin: str | None = None,
                     category: str | None = None,
                     strategy: Literal['all', 'private_debt', 'real_estate'] = 'all'):
    from datetime import timedelta
    periods = [r['periodo'] for r in rows(
        'SELECT DISTINCT periodo FROM cuotas_fi WHERE periodo<=CURRENT_DATE ORDER BY periodo DESC')]
    selected = period or (periods[0] if periods else None)
    if selected is None:
        return dict(periods=[], period=None, previous_period=None, rows=[], eligible=0, reported=0, comparable=0)
    if selected not in periods:
        raise HTTPException(422, 'Trimestre no disponible.')
    previous = selected.replace(month=((selected.month-1)//3)*3+1, day=1) - timedelta(days=1)
    metadata = {r['run_fondo']: r for r in rows('''SELECT f.run_fondo, c.categoria, c.grupo
        FROM fondos_inversion f LEFT JOIN (
            SELECT DISTINCT ON(run_fondo) run_fondo,categoria,grupo FROM categoria_fi_effective
            WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC
        ) c USING(run_fondo) WHERE f.rescatable IS FALSE''')}
    allowed = {run for run, r in metadata.items() if strategy == 'all'
               or (strategy == 'private_debt' and r['categoria'] == 'FI_DEUDA_PRIVADA')
               or (strategy == 'real_estate' and r['grupo'] == 'Inmobiliario')}
    universe = {f['run']: f for f in funds() if f['kind'] == 'fi' and f['currency'] == currency and f['run'] in allowed
                and (not admin or f['admin'] == admin) and (not category or f['category'] == category)}
    data = rows('SELECT * FROM cuotas_fi WHERE periodo IN (:period, :previous)',
                {'period': selected, 'previous': previous})
    prior = {r['run_fondo']: r for r in data if r['periodo'] == previous}
    result = []
    for r in data:
        if r['periodo'] != selected or r['run_fondo'] not in universe:
            continue
        f = universe[r['run_fondo']]
        result.append({**capital_changes(r, prior.get(r['run_fondo'])),
                       'run': f['run'], 'name': f['name'], 'admin': f['admin'],
                       'currency': currency, 'nav_date': f['data_date'], 'category': f['category'],
                       'rescatable': False})
    return dict(periods=periods, period=selected, previous_period=previous, rows=result,
                eligible=len(universe), reported=len(result), comparable=sum(r['has_previous'] for r in result))


def monthly_data(currency, kind, start, end, category=None, active_only=False):
    parts=[]
    for k,table,name,admin in [('fm','fondo_mutuo','nombre_fondo','razon_social_administradora'),('fi','fondos_inversion','razon_social','administrador')]:
        if kind != 'all' and kind != k:
            continue
        parts.append(f"""SELECT '{k}' kind,m.*,f.{name} fund_name,
        COALESCE(f.{admin},'Sin administradora') admin,
        COALESCE(c.nombre_cat,'Sin clasificación') category
        FROM mv_industry_monthly_{k} m JOIN {table} f USING(run_fondo)
        LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_{k}_effective
          WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
        WHERE m.currency=:currency AND m.month BETWEEN :start AND :end
          AND m.aum IS NOT NULL AND m.aum::text NOT IN ('NaN','Infinity','-Infinity')
          {'AND f.fecha_termino_operaciones IS NULL' if active_only and k=='fm' else 'AND f.vigente IS TRUE' if active_only else ''}
          AND m.aum>=0 AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)""")
    return rows(' UNION ALL '.join(parts),{'currency':currency,'start':start,'end':end,'category':category})


def market_snapshots(data):
    """Every administrator in a month uses exactly the same reporting date."""
    months=defaultdict(list)
    for r in data:
        months[r['month']].append(r)
    result=[]
    for month, available in sorted(months.items()):
        by_date=defaultdict(list)
        for r in available:
            by_date[r['latest_data_date']].append(r)
        # Prefer a date covering both fund types when both exist, then greatest
        # NAV-backed fund coverage. Dates never carry forward into another month.
        ref=max(by_date,key=lambda d:(len({r['kind'] for r in by_date[d]}),len(by_date[d]),d))
        sample=by_date[ref]
        total=sum(float(r['aum']) for r in sample)
        groups=defaultdict(list)
        for r in sample:
            groups[r['admin']].append(r)
        admins=[]
        for admin, funds in groups.items():
            aum=sum(float(r['aum']) for r in funds)
            weights=sorted((float(r['aum'])/aum*100 for r in funds),reverse=True) if aum>0 else []
            categories=defaultdict(float)
            for r in funds:
                categories[r['category']]+=float(r['aum'])
            admins.append({'admin':admin,'aum':aum,'funds':len(funds),'share':aum/total*100 if total>0 else None,
                'top5_pct':sum(weights[:5]) if weights else None,'hhi':sum(w*w for w in weights) if weights else None,
                'categories':[{'name':n,'aum':v,'share':v/aum*100 if aum>0 else None} for n,v in sorted(categories.items(),key=lambda p:-p[1])]})
        result.append({'month':month,'date':ref,'market_aum':total,'reported_funds':len(sample),
            'eligible_funds':len(available),'coverage_pct':len(sample)/len(available)*100,
            'administrators':sorted(admins,key=lambda r:-r['aum'])})
    return result


@router.get('/analytics/market')
def market_analysis(currency:str=Query('CLP',pattern='^[A-Z]{3}$'),kind:Literal['all','fm','fi']='all',
                    start:date=date(2025,1,1),end:date|None=None,category:str|None=None,active_only:bool=False):
    end=(end or date.today()).replace(day=1)
    start=start.replace(day=1)
    if start>end or end>date.today() or start<date(2020,1,1):
        raise HTTPException(422,'Rango inválido: use meses entre 2020 y el mes actual.')
    return {'currency':currency,'kind':kind,'months':market_snapshots(monthly_data(currency,kind,start,end,category,active_only=active_only))}


def aggregate_flows(data, adjustments, start, end, group_by, baseline_date):
    groups={}
    def entry(key):
        return groups.setdefault(key,{'name':key,'aportes':0.,'rescates':0.,'reported':0.,'migrations':0.,
                                     'opening_aum':0.,'opening_funds':0,'funds':set(),'has_flow':False})
    for r in data:
        key=r['admin'] if group_by=='admin' else r['category']
        g=entry(key)
        if r['month']<start and r['latest_data_date']==baseline_date:
            g['opening_aum']+=float(r['aum']);g['opening_funds']+=1
        if start<=r['month']<=end:
            nnm=finite(r.get('nnm'))
            if nnm is None:
                continue
            g['has_flow']=True;g['funds'].add(r['run_fondo'])
            g['aportes']+=finite(r.get('aportes')) or 0.
            g['rescates']+=finite(r.get('rescates')) or 0.
            g['reported']+=nnm
            g['migrations']+=adjustments.get((r['run_fondo'],r['month']),0.)
    result=[]
    for g in groups.values():
        if not g.pop('has_flow'):
            continue
        g['funds']=len(g['funds']);g['net']=g['reported']-g['migrations']
        g['flow_pct']=g['net']/g['opening_aum']*100 if g['opening_aum']>0 else None
        result.append(g)
    return sorted(result,key=lambda r:-r['net'])


@router.get('/analytics/flows')
def flow_analysis(currency:str=Query('CLP',pattern='^[A-Z]{3}$'),month:date|None=None,
                  period:Literal['month','ytd']='month',group_by:Literal['admin','category']='admin'):
    month=(month or date.today()).replace(day=1)
    if month>date.today() or month<date(2020,1,1):
        raise HTTPException(422,'Mes fuera del rango disponible.')
    start=month if period=='month' else month.replace(month=1)
    baseline=(start.replace(day=1).toordinal()-1)
    baseline=date.fromordinal(baseline).replace(day=1)
    data=monthly_data(currency,'fm',baseline,month)
    prior=[r for r in data if r['month']==baseline]
    snapshots=market_snapshots(prior)
    baseline_date=snapshots[0]['date'] if snapshots else None
    current=[r for r in data if start<=r['month']<=month]
    dates=[r['latest_data_date'] for r in current]
    cutoff=max(dates) if dates else min(date.today(),month.replace(day=monthrange(month.year,month.month)[1]))
    adj=rows("""SELECT target_run_fondo run,DATE_TRUNC('month',event_date)::date AS month,SUM(amount) amount
        FROM fm_flow_adjustments WHERE currency=:currency AND status IN ('auto_confirmed','confirmed')
        AND event_date BETWEEN :start AND :cutoff GROUP BY target_run_fondo,DATE_TRUNC('month',event_date)::date""",
        {'currency':currency,'start':start,'cutoff':cutoff})
    adjustments={(r['run'],r['month']):float(r['amount']) for r in adj}
    return {'currency':currency,'start':start,'end':cutoff,'baseline_date':baseline_date,
        'partial':month==date.today().replace(day=1),'group_by':group_by,
        'rows':aggregate_flows(data,adjustments,start,month,group_by,baseline_date)}


def position_key(p):
    identifier=(p.get('identifier') or '').strip()
    if not identifier or identifier in ('NaN','0'):
        return None
    return (p.get('source'),identifier,p.get('instrument_type'),p.get('issuer_rut') or p.get('issuer'))


def grouped_positions(positions):
    result={}
    for p in positions:
        key=position_key(p)
        if key is None:
            continue
        item=result.setdefault(key,{'position':p,'weight':0.,'missing':False})
        w=finite(p.get('weight'))
        if w is None:
            item['missing']=True
        else:
            item['weight']+=w
    for item in result.values():
        if item['missing']:
            item['weight']=None
    return result


def concentration(positions, dimension):
    values=defaultdict(float)
    for p in positions:
        weight=finite(p.get('weight'))
        if weight is not None and weight>=0:
            key=p.get(dimension) or 'Sin identificar'
            values[key]+=weight
    coverage=sum(values.values())
    buckets=[{'name':k,'weight':v,'share_covered':v/coverage*100 if coverage>0 else None} for k,v in sorted(values.items(),key=lambda p:-p[1])]
    return {'covered_weight':coverage,'top5_pct_covered':sum(b['weight'] for b in buckets[:5])/coverage*100 if coverage>0 else None,
        'hhi_covered':sum((b['weight']/coverage*100)**2 for b in buckets) if coverage>0 else None,'buckets':buckets}


def portfolio_diff(before, after):
    old=grouped_positions(before);new=grouped_positions(after);changes=[]
    for key in old.keys()|new.keys():
        a=old.get(key);b=new.get(key)
        old_weight=a['weight'] if a else 0.;new_weight=b['weight'] if b else 0.
        delta=new_weight-old_weight if old_weight is not None and new_weight is not None else None
        status='new' if a is None else 'exited' if b is None else 'unknown' if delta is None else 'increased' if delta>0.0001 else 'decreased' if delta < -0.0001 else 'unchanged'
        p=(b or a)['position']
        changes.append({'identifier':p['identifier'],'issuer':p['issuer'],'instrument':p['instrument_name'],'country':p.get('country'),
            'before':old_weight,'after':new_weight,'delta':delta,'status':status})
    return sorted(changes,key=lambda p:-(abs(p['delta']) if p['delta'] is not None else -1))


@router.get('/analytics/portfolio/{kind}/{run}')
def portfolio_analysis(kind:Literal['fm','fi'],run:str,before:date,after:date):
    if before>=after or after>date.today():
        raise HTTPException(422,'Seleccione un período inicial anterior al final.')
    old=portfolio(kind,run,before);new=portfolio(kind,run,after)
    if before not in old['periods'] or after not in new['periods']:
        raise HTTPException(422,'Ambos períodos deben tener una cartera reportada.')
    if not old['positions'] or not new['positions']:
        raise HTTPException(422,'No hay posiciones comparables en ambos períodos.')
    def coverage(p):
        values=p['positions']
        return {'rows':len(values),'identified_rows':sum(position_key(x) is not None for x in values),
                'missing_weight_rows':sum(finite(x.get('weight')) is None for x in values),
                'reported_weight':sum(finite(x.get('weight')) or 0. for x in values)}
    return {'before':before,'after':after,'coverage_before':coverage(old),'coverage_after':coverage(new),
        'changes':portfolio_diff(old['positions'],new['positions']),
        'concentration':{label:{'before':concentration(old['positions'],field),'after':concentration(new['positions'],field)}
            for label,field in [('issuer','issuer'),('instrument','instrument_name'),('country','country')]}}

from .currency_exposure import router as exposure_router
from .administrators import router as administrators_router
router.include_router(exposure_router)
router.include_router(administrators_router)

from .administrator_timeseries import router as timeseries_router
router.include_router(timeseries_router)
