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
