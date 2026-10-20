"""Comparable, native-currency analysis of reported FI shareholder relationships."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from . import ownership as base

router = APIRouter()


def rut_key(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[0-9.]+(?:-[0-9kK])?', value):
        return None
    return value.split('-')[0].replace('.', '').lstrip('0') or None


@lru_cache(maxsize=16)
def categories(period, bucket):
    from .router import rows
    return {r['run']: r for r in rows('''SELECT DISTINCT ON(run_fondo) run_fondo run,
        nombre_cat category,periodo category_date FROM categoria_fi_effective WHERE periodo<=:period
        ORDER BY run_fondo,periodo DESC''', {'period': period})}


@lru_cache(maxsize=24)
def source(period, bucket):
    cats = categories(period, bucket)
    records = [dict(r, rut=rut_key(r['rut']), **{k:v for k,v in cats.get(r['run'], {}).items() if k!='run'})
               for r in base.snapshot(period, bucket)]
    # Normalized aliases of one RUT must count only once within a fund.
    grouped = base.positions(records)
    by_fund = defaultdict(list)
    for r in grouped: by_fund[r['run']].append(r)
    for values in by_fund.values():
        if sum(r['weight'] or 0 for r in values)>100.01:
            for r in values: r['invalid_total']=True
    return grouped


def amount_rows(records, navs, currency):
    result = []
    for r in records:
        nav = navs.get(r['run'], {})
        known = nav.get('aum') is not None and nav.get('currency') == currency and r['weight'] is not None and not r.get('invalid_total')
        result.append(dict(r, category=r.get('category') or 'Sin categoría',
                           amount=r['weight']/100*nav['aum'] if known else None,
                           currency=nav.get('currency'), nav_date=nav.get('date')))
    return result


def total(rows):
    values = [r['amount'] for r in rows if r['amount'] is not None]
    return sum(values) if values else None


def coverage(rows, currency):
    return dict(amount=total(rows), positions=len(rows), valued=sum(r['amount'] is not None for r in rows),
                missing=sum(r['amount'] is None and r.get('currency') in (None,currency) for r in rows),
                other_currency=sum(r.get('currency') not in (None,currency) for r in rows))


def grouped(rows, key, currency):
    groups = defaultdict(list)
    for r in rows: groups[r.get(key) or 'Sin información'].append(r)
    return sorted([dict(name=k, funds=len({r['run'] for r in v}), owners=len({r['rut'] for r in v if r['rut']}),
                        **coverage(v,currency)) for k,v in groups.items()],
                  key=lambda r:(r['amount'] is None,-(r['amount'] or 0),r['name']))


def btg_share(rows, currency):
    btg = [r for r in rows if 'btg pactual' in base.normalize(r['admin'])]
    c = coverage(rows,currency); b = coverage(btg,currency)
    # Zero means no valued BTG position within the observed, valued universe.
    btg_amount = b['amount'] if b['amount'] is not None else (0 if not b['missing'] and c['amount'] is not None else None)
    return {
        'total':c, 'btg':{**b,'amount':btg_amount},
        'share':100*btg_amount/c['amount'] if btg_amount is not None and c['amount'] is not None and c['amount']>0 else None,
    }


def movement_effect(old, new, old_nav, new_nav, currency):
    if (old is None or new is None or old.get('invalid_total') or new.get('invalid_total') or old['weight'] is None or new['weight'] is None
        or old_nav.get('currency') != currency or new_nav.get('currency') != currency
        or old_nav.get('aum') is None or new_nav.get('aum') is None):
        return dict(amount_change=None, patrimonio_effect=None, ownership_effect=None)
    # Exact two-factor bridge. Fund patrimonio changes include returns AND fund flows.
    w0, w1 = old['weight']/100, new['weight']/100
    a0, a1 = old_nav['aum'], new_nav['aum']
    return dict(amount_change=w1*a1-w0*a0, patrimonio_effect=w0*(a1-a0), ownership_effect=(w1-w0)*a1)


def context(period, nav_days):
    if nav_days not in (7,31): raise HTTPException(422,'NAV age must be 7 or 31 days')
    selected, periods, bucket = base.selected_period(period)
    records = source(selected,bucket) if selected else []
    return selected, periods, bucket, records


@router.get('/ownership-analysis')
def overview(period: date | None = None, currency: str = Query('CLP',pattern=r'^[A-Z]{3}$'),
             nav_days: int = 31, admin: str = '', category: str = '', person: Literal['','J','N'] = '',
             run: str = '', search: str = Query('',max_length=150), state: str = '',
             sort: Literal['delta','amount_change','after_amount'] = 'delta', direction: Literal['asc','desc'] = 'desc',
             offset: int = Query(0,ge=0), limit: int = Query(50,ge=1,le=200)):
    selected,periods,bucket,current = context(period,nav_days)
    previous = base.shift(selected,-3) if selected else None
    prior = source(previous,bucket) if previous else []
    navs = base.nav_snapshot(selected,bucket,nav_days) if selected else {}
    old_navs = base.nav_snapshot(previous,bucket,nav_days) if previous else {}
    def scope(r):
        return (not admin or r['admin']==admin) and (not category or (r.get('category') or 'Sin categoría')==category) and (not person or r['person_type']==person) and (not run or r['run']==run)
    valued = amount_rows([r for r in current if scope(r) and r['rut']],navs,currency)
    changes = [r for r in base.changes(current,prior) if scope(r)]
    old_map = {(r['rut'],r['run']):r for r in prior}
    new_map = {(r['rut'],r['run']):r for r in current}
    for r in changes:
        old,new = old_map.get((r['rut'],r['run'])),new_map.get((r['rut'],r['run']))
        r.update(movement_effect(old,new,old_navs.get(r['run'],{}),navs.get(r['run'],{}),currency))
        r['before_amount'] = total(amount_rows([old],old_navs,currency)) if old else None
        r['after_amount'] = total(amount_rows([new],navs,currency)) if new else None
        r['category'] = r.get('category') or 'Sin categoría'
    counts = {s:sum(r['state']==s for r in changes) for s in ['increased','decreased','appears','unlisted','no_report','unknown','stable']}
    q = base.normalize(search)
    changes = [r for r in changes if (not state or r['state']==state) and q in base.normalize(f"{r['name']} {r['rut']} {r['fund_name']}")]
    sign = -1 if direction=='desc' else 1
    changes.sort(key=lambda r:(r.get(sort) is None,sign*(r.get(sort) or 0),r['rut'],r['run']))
    return dict(period=selected, previous=previous, periods=periods,currency=currency,
                admins=sorted({r['admin'] for r in current+prior if r['admin']}),
                categories=sorted({r.get('category') or 'Sin categoría' for r in current+prior}),
                funds=sorted({r['run']:dict(run=r['run'],name=r['fund_name']) for r in current+prior}.values(),key=lambda r:r['name']),
                summary=dict(owners=len({r['rut'] for r in valued}),funds=len({r['run'] for r in valued}),**btg_share(valued,currency)),
                by_admin=grouped(valued,'admin',currency),by_category=grouped(valued,'category',currency),
                movements=counts,rows=changes[offset:offset+limit],total=len(changes))


@router.get('/ownership-analysis/{rut}')
def profile(rut: str, period: date | None = None, currency: str = Query('CLP',pattern=r'^[A-Z]{3}$'), nav_days: int = 31):
    key = rut_key(rut)
    if not key: raise HTTPException(422,'An identified RUT is required')
    selected,periods,bucket,current = context(period,nav_days)
    dates = [base.shift(selected,-3*i) for i in reversed(range(8))] if selected else []
    records = {d:[r for r in source(d,bucket) if r['rut']==key] for d in dates}
    navs = {d:base.nav_snapshot(d,bucket,nav_days) for d in dates}
    valued = {d:amount_rows(records[d],navs[d],currency) for d in dates}
    history = [dict(period=d,**btg_share(valued[d],currency)) for d in dates]
    current_rows = valued.get(selected,[])
    all_runs = sorted({r['run'] for rows in valued.values() for r in rows})
    maps = {d:{r['run']:r for r in v} for d,v in valued.items()}
    amounts = [dict(run=run,amounts=[maps[d].get(run,{}).get('amount') for d in dates],
                    nav_dates=[maps[d].get(run,{}).get('nav_date') for d in dates]) for run in all_runs]
    bridge = []
    if len(dates)>1:
        old = {r['run']:r for r in records[dates[-2]]}
        for r in current_rows:
            effect=movement_effect(old.get(r['run']),r,navs[dates[-2]].get(r['run'],{}),navs[selected].get(r['run'],{}),currency)
            bridge.append(dict(run=r['run'],fund_name=r['fund_name'],**effect))
    return dict(period=selected,currency=currency,dates=dates,rows=current_rows,amounts=amounts,history=history,bridge=bridge,
                **btg_share(current_rows,currency),by_admin=grouped(current_rows,'admin',currency),by_category=grouped(current_rows,'category',currency))


def compare_rows(records,navs,currency,admin_a,admin_b):
    groups=defaultdict(list)
    for r in amount_rows(records,navs,currency):
        if r['rut'] and r['admin'] in (admin_a,admin_b):groups[r['rut']].append(r)
    result=[]
    for rut,items in groups.items():
        a=[r for r in items if r['admin']==admin_a];b=[r for r in items if r['admin']==admin_b]
        result.append(dict(rut=rut,name=items[0]['name'],side='shared' if a and b else 'a' if a else 'b',
                           a=coverage(a,currency),b=coverage(b,currency),combined=coverage(items,currency),funds=items))
    return result


@router.get('/ownership-comparison')
def comparison(admin_a: str = '', admin_b: str = '', period: date | None = None,
               currency: str = Query('CLP',pattern=r'^[A-Z]{3}$'),nav_days: int = 31,
               category: str = '', person: Literal['','J','N'] = ''):
    selected,periods,bucket,records=context(period,nav_days)
    admins=sorted({r['admin'] for r in records if r['admin']})
    if admin_a and admin_a==admin_b:raise HTTPException(422,'Select two different administrators')
    if any(a and a not in admins for a in (admin_a,admin_b)):raise HTTPException(422,'Administrator unavailable at this quarter')
    navs=base.nav_snapshot(selected,bucket,nav_days) if selected else {}
    filtered=[r for r in records if (not category or (r.get('category') or 'Sin categoría')==category) and (not person or r['person_type']==person)]
    items=compare_rows(filtered,navs,currency,admin_a,admin_b) if admin_a and admin_b else []
    positions=[r for i in items for r in i['funds']]
    amounts=sorted([r['combined']['amount'] for r in items if r['combined']['amount'] is not None],reverse=True)
    merged=total(positions)
    return dict(period=selected,periods=periods,currency=currency,admins=admins,
                categories=sorted({r.get('category') or 'Sin categoría' for r in records}),rows=items,
                summary=dict(shared=sum(r['side']=='shared' for r in items),only_a=sum(r['side']=='a' for r in items),
                  only_b=sum(r['side']=='b' for r in items),unique=len(items),
                  a=coverage([r for r in positions if r['admin']==admin_a],currency),
                  b=coverage([r for r in positions if r['admin']==admin_b],currency),combined=coverage(positions,currency),
                  top5_pct=100*sum(amounts[:5])/merged if merged and merged>0 else None),
                by_category=grouped(positions,'category',currency))
