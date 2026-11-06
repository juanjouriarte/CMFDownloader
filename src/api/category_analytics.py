"""Category snapshots and competition on one shared monthly NAV/EEFF universe."""
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException
from .classification_admin import classification_cache_epoch
from .deps import CacheHook
from .fx import Currency, month_ends
from .industry_workspace.administrator_equity import mixed_samples, report_data
from .industry_workspace.administrators import month_shift
from src.fx import Rates

router = APIRouter(prefix='/industry-workspace/category-analysis', tags=['Industry workspace'])


def category_key(row):
    return json.dumps([row['kind'], row.get('category_type') or '',
                      row.get('category_group') or '', row.get('category') or 'Sin clasificación'],
                     ensure_ascii=False, separators=(',', ':'))


def growth(value, before):
    return (value / before - 1) * 100 if value is not None and before is not None and before > 0 else None


def share(value, total):
    return value / total * 100 if value is not None and total is not None and total > 0 else None


def summarize(sample, coverage, metadata):
    """Keep empty observations null and never invent zero-weight exits in history."""
    by_month = defaultdict(list)
    for source in sample:
        row = dict(source, **metadata.get((source['kind'], source['run_fondo']), {}))
        row['category_id'] = category_key(row)
        by_month[row['month']].append(row)
    months, current_funds = [], []
    for month, meta in sorted(coverage.items()):
        observed = by_month[month]
        total = sum((r['aum'] for r in observed), Decimal(0)) if observed and not meta['missing_fx_funds'] else None
        groups = defaultdict(list)
        for row in observed:
            groups[row['category_id']].append(row)
        categories = []
        for key, funds in groups.items():
            amount = sum((r['aum'] for r in funds), Decimal(0))
            managers = defaultdict(list)
            for fund in funds:
                managers[fund['admin']].append(fund)
            admins = []
            for admin, own in managers.items():
                aum = sum((r['aum'] for r in own), Decimal(0))
                admins.append(dict(admin=admin, aum=aum, share=share(aum, amount), funds=len(own)))
            btg = sum((a['aum'] for a in admins if 'BTG' in a['admin'].upper()), Decimal(0))
            first = funds[0]
            categories.append(dict(id=key, kind=first['kind'], name=first['category'],
                category_type=first.get('category_type'), category_group=first.get('category_group'),
                aum=amount, share=share(amount, total), funds=len(funds), administrators=len(admins),
                nav_funds=sum(r['aum_source']=='nav' for r in funds),
                eeff_funds=sum(r['aum_source']=='quarterly_report' for r in funds),
                btg_aum=btg, btg_share=share(btg, amount),
                admins=sorted(admins, key=lambda a: -a['aum'])))
        months.append(dict(month=month, date=meta['date'], total=total,
            nav_funds=meta['nav_funds'], eeff_funds=meta['eeff_funds'],
            missing_fx_funds=meta['missing_fx_funds'], excluded_reports=len(meta['excluded_reports']),
            first_report_date=meta['first_report_date'], last_report_date=meta['last_report_date'],
            categories=sorted(categories, key=lambda r: -r['aum'])))
        current_funds = [dict(category_id=r['category_id'], kind=r['kind'], run=r['run_fondo'],
            name=r['fund_name'], admin=r['admin'], aum=r['aum'], date=r['valuation_date'],
            currency=r.get('native_currency') or r['currency'], source=r['aum_source']) for r in observed]
    return dict(months=months, funds=current_funds)


@lru_cache(maxsize=3)
def dataset(kind, currency, source, start, end, bucket):
    from .industry_workspace.router import rows, monthly_data
    tables = [f'mv_industry_monthly_{k}' for k in ('fm', 'fi') if kind in ('all', k)]
    calendar = rows('SELECT MAX(latest_data_date) latest FROM (' + ' UNION ALL '.join(
        f'SELECT latest_data_date FROM {table} WHERE latest_data_date<=CURRENT_DATE' for table in tables
    ) + ') d')[0]['latest']
    latest = calendar or date.today()
    closing = min(end or latest, latest)
    # Extra year supports MoM/YoY, independently of the visible history range.
    visible_start = max(date(2020, 1, 1), start or month_shift(closing.replace(day=1), -11))
    earliest = max(date(2020, 1, 1), month_shift(visible_start, -12))
    if visible_start > closing:
        raise HTTPException(422, 'El período empieza después del último dato disponible.')
    cutoffs = dict(month_ends(earliest, closing))
    raw = monthly_data(currency, kind, earliest, closing.replace(day=1), active_only=True) if source!='quarterly_report' else []
    candidates, reports = report_data(earliest, closing, kind) if source!='nav' else ([], [])
    book = Rates.load(earliest-timedelta(days=185), closing) if currency is None else None
    sample, coverage = mixed_samples(raw, candidates, reports, cutoffs, source, currency, rates=book)
    metadata = {}
    for k in ('fm', 'fi'):
        if kind in ('all', k):
            for r in rows(f'''SELECT DISTINCT ON(run_fondo) run_fondo,
                COALESCE(nombre_cat,'Sin clasificación') category, tipo category_type, grupo category_group
                FROM categoria_{k}_effective WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC'''):
                metadata[(k, r['run_fondo'])] = {key: value for key, value in r.items() if key!='run_fondo'}
    result = summarize(sample, coverage, metadata)
    return dict(result, latest=latest, from_date=visible_start, to_date=closing,
                currency=currency or 'CLP', consolidated=currency is None, kind=kind, source=source)


def present(data, selected=None):
    months = data['months']
    current = months[-1]
    lookup = {m['month']: {c['id']:c for c in m['categories']} for m in months}
    previous = lookup.get(month_shift(current['month'], -1), {})
    year = lookup.get(month_shift(current['month'], -12), {})
    ranking = []
    for item in current['categories']:
        row = {k:v for k,v in item.items() if k!='admins'}
        row.update(mom=growth(row['aum'], previous.get(row['id'], {}).get('aum')),
                   yoy=growth(row['aum'], year.get(row['id'], {}).get('aum')))
        ranking.append(row)
    points = []
    for month in months:
        if month['month'] < data['from_date']:
            continue
        category = lookup[month['month']].get(selected)
        previous_admins = {a['admin']:a for a in lookup.get(month_shift(month['month'], -1), {}).get(selected, {}).get('admins', [])}
        history_admins = []
        for admin in category['admins'] if category else []:
            old_share = previous_admins.get(admin['admin'], {}).get('share')
            history_admins.append(dict(admin, share_change=admin['share']-old_share if admin['share'] is not None and old_share is not None else None))
        points.append(dict(month=month['month'], date=month['date'], industry_aum=month['total'],
            aum=category['aum'] if category else None, share=category['share'] if category else None,
            funds=category['funds'] if category else 0, missing_fx_funds=month['missing_fx_funds'],
            admins=history_admins))
    category = lookup[current['month']].get(selected)
    admins = []
    old = {r['admin']:r for r in previous.get(selected, {}).get('admins', [])}
    for row in category['admins'] if category else []:
        before = old.get(row['admin'])
        admins.append(dict(row, mom=growth(row['aum'], before['aum'] if before else None),
            share_change=row['share']-before['share'] if before and row['share'] is not None and before['share'] is not None else None))
    funds = [dict(r, share=share(r['aum'], category['aum'])) for r in data['funds']
             if category and r['category_id']==selected]
    return dict(**{k:v for k,v in data.items() if k not in ('months','funds')},
        ranking=ranking, coverage={k:v for k,v in current.items() if k!='categories'},
        selected=selected, history=points if selected else [], administrators=admins, funds=funds)


@router.get('')
def category_analysis(_:CacheHook, kind:Literal['all','fm','fi']='all', currency:Currency=None,
        source:Literal['all','nav','quarterly_report']='all', from_date:date|None=None,
        to_date:date|None=None, category_id:str|None=None):
    start = from_date.replace(day=1) if from_date else None
    if (start and start<date(2020,1,1)) or (to_date and not date(2020,1,1)<=to_date<=date.today()) or (start and to_date and start>to_date):
        raise HTTPException(422, 'Selecciona fechas desde 2020 hasta hoy.')
    return present(dataset(kind,currency,source,start,to_date,classification_cache_epoch()),category_id)
