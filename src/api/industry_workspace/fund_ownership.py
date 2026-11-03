"""Reported ownership by fund and quarter. Absence is never inferred as zero."""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, HTTPException
from .ownership import weight
from .ownership_analysis import rut_key

router = APIRouter()


def quarter_dates(start, end):
    result=[]
    for year in range(start.year,end.year+1):
        for month in (3,6,9,12):
            d=date(year,month,monthrange(year,month)[1])
            if start<=d<=end:result.append(d)
    return result


def ownership_history(records):
    if not records:return dict(dates=[],rows=[],coverage=[])
    dates=sorted(set(quarter_dates(min(r['periodo'] for r in records),max(r['periodo'] for r in records))) |
                 {r['periodo'] for r in records})
    grouped=defaultdict(list)
    for index,r in enumerate(records):
        # Missing IDs cannot establish continuity, even if rank/name repeats.
        rut=rut_key(r.get('rut'))
        identity='rut:'+rut if rut else f"unknown:{r['periodo']}:{r.get('rank')}:{index}"
        grouped[(r['periodo'],identity)].append(r)
    by_date=defaultdict(dict);identities={}
    for (period,identity),group in sorted(grouped.items()):
        values=[weight(r.get('weight')) for r in group]
        total=sum(values) if all(v is not None for v in values) else None
        item=dict(id=identity,rut=rut_key(group[0].get('rut')),
                  name=group[0].get('name') or 'Aportante sin identificar',weight=weight(total))
        by_date[period][identity]=item
        identities[identity]=item
    coverage=[]
    for d in dates:
        items=list(by_date[d].values())
        # Check original rows as an invalid grouped value must not conceal a >100% filing.
        original=[weight(r.get('weight')) for r in records if r['periodo']==d]
        subtotal=sum(v for v in original if v is not None)
        invalid=subtotal>100.01
        coverage.append(dict(period=d,reported=bool(items),holders=len(items),invalid_total=invalid,
                             reported_weight=subtotal if original and all(v is not None for v in original) else None))
    result=[]
    for identity,info in identities.items():
        weights=[];states=[]
        for d,c in zip(dates,coverage):
            item=by_date[d].get(identity)
            state=('no_report' if not c['reported'] else 'invalid_report' if c['invalid_total'] else
                   'not_listed' if item is None else 'missing_weight' if item['weight'] is None else 'reported')
            states.append(state)
            weights.append(item['weight'] if state=='reported' else None)
        changes=[None]
        for i in range(1,len(dates)):
            adjacent=(dates[i].year*12+dates[i].month-dates[i-1].year*12-dates[i-1].month==3
                      and all(d.month in (3,6,9,12) and d.day==monthrange(d.year,d.month)[1]
                              for d in (dates[i-1],dates[i])))
            changes.append(weights[i]-weights[i-1] if adjacent and weights[i] is not None and weights[i-1] is not None else None)
        result.append(dict(id=identity,rut=info['rut'],name=info['name'],weights=weights,changes=changes,states=states))
    result.sort(key=lambda r:(r['weights'][-1] is None,-(r['weights'][-1] or 0),r['name']))
    return dict(dates=dates,rows=result,coverage=coverage)


@router.get('/fund-ownership/{run}')
def fund_ownership(run: str):
    from .router import rows
    fund=rows('SELECT razon_social name FROM fondos_inversion WHERE run_fondo=:run',dict(run=run))
    if not fund:raise HTTPException(404,'Investment fund not found')
    records=rows('''SELECT periodo,rank,rut,COALESCE(nombre_canonical,nombre) name,pct_propiedad weight
      FROM aportantes_fi WHERE run_fondo=:run AND periodo<=CURRENT_DATE ORDER BY periodo,rank''',dict(run=run))
    return dict(run=run,name=fund[0]['name'],**ownership_history(records))


@router.get('/ownership-funds')
def ownership_funds():
    from .router import rows
    return rows('''SELECT 'fi' kind,f.run_fondo run,f.razon_social name,f.administrador admin,
      f.vigente active,c.nombre_cat category,c.tipo category_type,c.grupo category_group,
      p.first_date,p.last_date,p.periods
      FROM fondos_inversion f JOIN (
        SELECT run_fondo,min(periodo) first_date,max(periodo) last_date,count(DISTINCT periodo) periods
        FROM aportantes_fi WHERE periodo<=CURRENT_DATE GROUP BY run_fondo
      ) p USING(run_fondo)
      LEFT JOIN LATERAL (SELECT nombre_cat,tipo,grupo FROM categoria_fi_effective c
        WHERE c.run_fondo=f.run_fondo AND periodo<=CURRENT_DATE ORDER BY periodo DESC LIMIT 1) c ON true
      ORDER BY f.razon_social''')


def comparison_period(funds, available, requested=None):
    periods={f['run']:{r['periodo'] for r in available if r['run']==f['run']} for f in funds}
    union=set().union(*periods.values())
    common=set.intersection(*periods.values()) if periods else set()
    selected=requested or (max(common) if common else max(union) if union else None)
    return selected,sorted(union,reverse=True),sorted(common,reverse=True)


def ownership_comparison(funds, records, period):
    if period is None:
        return dict(funds=[dict(f,reported=False,previous_reported=False,holders=0,
                               invalid_total=False,reported_weight=None) for f in funds],
                    rows=[],previous_period=None)
    previous=quarter_dates(date(period.year-1,1,1),period)[-2]
    matrix={};columns=[]
    for index,fund in enumerate(funds):
        run=fund['run']
        source=[r for r in records if r['run']==run and r['periodo'] in (previous,period)]
        history=ownership_history(source)
        mapping={d:i for i,d in enumerate(history['dates'])}
        current=mapping.get(period);old=mapping.get(previous)
        coverage=history['coverage'][current] if current is not None else dict(reported=False,holders=0,invalid_total=False,reported_weight=None)
        old_coverage=history['coverage'][old] if old is not None else dict(reported=False,invalid_total=False)
        columns.append(dict(fund,**{k:v for k,v in coverage.items() if k!='period'},previous_reported=old_coverage['reported']))
        current_ids={rut_key(r.get('rut')) for r in source if r['periodo']==period and rut_key(r.get('rut'))}
        for holder in history['rows']:
            identity=holder['id'] if holder['rut'] else run+':'+holder['id']
            row=matrix.setdefault(identity,dict(id=identity,rut=holder['rut'],name=holder['name'],cells=[None]*len(funds)))
            state=holder['states'][current] if current is not None else 'no_report'
            present=(holder['rut'] in current_ids) if holder['rut'] else (current is not None and period.isoformat() in holder['id'])
            row['cells'][index]=dict(weight=holder['weights'][current] if current is not None else None,
                previous=holder['weights'][old] if old is not None else None,
                change=holder['changes'][current] if current is not None else None,state=state,present=present)
    for row in matrix.values():
        for i,cell in enumerate(row['cells']):
            if cell is None:
                state='no_report' if not columns[i]['reported'] else 'invalid_report' if columns[i]['invalid_total'] else 'not_listed'
                row['cells'][i]=dict(weight=None,previous=None,change=None,state=state,present=False)
        row['funds']=sum(c['present'] for c in row['cells'])
    return dict(funds=columns,previous_period=previous,rows=sorted(matrix.values(),key=lambda r:(-r['funds'],r['name'])))


@router.get('/fund-ownership-comparison')
def compare_fund_ownership(runs: str, period: date | None = None):
    import re
    from .router import rows
    requested=list(dict.fromkeys(runs.split(',')))
    if not requested or len(runs)>6000 or any(not re.fullmatch(r'\d{1,10}',r) for r in requested):
        raise HTTPException(422,'Seleccione RUN de fondos válidos.')
    if period and (period>date.today() or period.month not in (3,6,9,12) or period.day!=monthrange(period.year,period.month)[1]):
        raise HTTPException(422,'Seleccione un cierre trimestral publicado.')
    found=rows('''SELECT run_fondo run,razon_social name,administrador admin
      FROM fondos_inversion WHERE run_fondo=ANY(:runs)''',dict(runs=requested))
    by_run={f['run']:f for f in found}
    if any(r not in by_run for r in requested):raise HTTPException(404,'Investment fund not found')
    funds=[by_run[r] for r in requested]
    available=rows('''SELECT DISTINCT run_fondo run,periodo FROM aportantes_fi
      WHERE run_fondo=ANY(:runs) AND periodo<=CURRENT_DATE''',dict(runs=requested))
    selected,periods,common=comparison_period(funds,available,period)
    records=[]
    if selected:
        previous=quarter_dates(date(selected.year-1,1,1),selected)[-2]
        records=rows('''SELECT run_fondo run,periodo,rank,rut,COALESCE(nombre_canonical,nombre) name,pct_propiedad weight
          FROM aportantes_fi WHERE run_fondo=ANY(:runs) AND periodo IN (:period,:previous)
          ORDER BY periodo,run_fondo,rank''',dict(runs=requested,period=selected,previous=previous))
    return dict(period=selected,periods=periods,common_periods=common,
                default_basis='selected' if period else 'latest_common' if common else 'latest_available',
                **ownership_comparison(funds,records,selected))
