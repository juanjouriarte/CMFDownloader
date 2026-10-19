"""Reported FI ownership: percentages/counts, without portfolio-derived currency estimates."""
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from math import isfinite
from time import time
from typing import Literal
import unicodedata

from fastapi import APIRouter, HTTPException, Query
from .currency_exposure import shift

router = APIRouter()


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value or '').lower()) if not unicodedata.combining(c))


def weight(value):
    try:
        value = float(value)
        return value if isfinite(value) and 0 <= value <= 100 else None
    except (TypeError, ValueError):
        return None


def positions(records):
    grouped = defaultdict(list)
    for r in records:
        # An unidentified reported holder remains local to its fund/report/rank.
        grouped[(r['run'], r.get('rut') or f"unknown:{r.get('rank')}")].append(r)
    result = []
    for group in grouped.values():
        values = [weight(r.get('weight')) for r in group]
        total = sum(values) if all(v is not None for v in values) else None
        types = {r.get('person_type') for r in group}
        result.append({**group[0], 'weight': weight(total),
                       'person_type': 'N' if types == {'N'} else 'J' if None not in types and 'N' not in types else None})
    totals = defaultdict(float)
    for r in result: totals[r['run']] += r['weight'] or 0
    for r in result: r['invalid_total'] = totals[r['run']] > 100.01
    return result


def changes(current, previous):
    now = {(r['run'], r['rut']): r for r in current if r.get('rut')}
    before = {(r['run'], r['rut']): r for r in previous if r.get('rut')}
    now_reports, old_reports = {r['run'] for r in current}, {r['run'] for r in previous}
    result = []
    for key in now.keys() | before.keys():
        a, b = now.get(key), before.get(key)
        new, old = a.get('weight') if a else None, b.get('weight') if b else None
        delta = new - old if new is not None and old is not None else None
        state = ('no_report' if key[0] not in now_reports or key[0] not in old_reports else
                 'appears' if b is None else 'unlisted' if a is None else
                 'unknown' if delta is None else 'increased' if delta > .00005 else
                 'decreased' if delta < -.00005 else 'stable')
        result.append({**(a or b), 'before': old, 'after': new, 'delta': delta, 'state': state,
                       'current_report': key[0] in now_reports, 'previous_report': key[0] in old_reports})
    return result


def directory(records):
    groups = defaultdict(list)
    for r in records:
        if r.get('rut'):
            groups[r['rut']].append(r)
    result = []
    for rut, group in groups.items():
        weights = [r['weight'] for r in group if r['weight'] is not None]
        types = {r['person_type'] for r in group}
        result.append(dict(rut=rut, name=group[0]['name'], funds=len({r['run'] for r in group}),
                           admins=len({r['admin'] for r in group if r['admin']}),
                           person_type=next(iter(types)) if len(types) == 1 else None,
                           max_weight=max(weights) if weights else None,
                           btg_funds=len({r['run'] for r in group if 'btg pactual' in normalize(r['admin'])})))
    return sorted(result, key=lambda r: (-r['funds'], -r['admins'], r['name'] or '', r['rut']))


def value_positions(records, navs, currency):
    amounts, dates = [], []
    missing = other = 0
    for r in records:
        nav = navs.get(r['run'])
        if not nav or nav.get('aum') is None or not nav.get('currency'):
            missing += 1
        elif nav['currency'] != currency:
            other += 1
        elif r['weight'] is None or r.get('invalid_total'):
            missing += 1
        else:
            amounts.append(r['weight'] / 100 * nav['aum'])
            dates.append(nav['date'])
    return dict(aum=sum(amounts) if amounts else None, currency=currency,
                valued_funds=len(amounts), unvalued_funds=missing, other_currency_funds=other,
                nav_from=min(dates) if dates else None, nav_to=max(dates) if dates else None)


def sort_directory(data, sort, direction):
    sign = -1 if direction == 'desc' else 1
    return sorted(data, key=lambda r: (r.get(sort) is None, sign * (r.get(sort) or 0), r['name'] or '', r['rut']))


@lru_cache(maxsize=16)
def nav_snapshot(period, bucket, max_age=31):
    from .router import rows
    from src.api.industry import _currency
    # One whole-fund snapshot. Never carry different series from different days.
    data = rows(f"""WITH latest AS (
        SELECT run_fondo, MAX(fecha) fecha FROM valores_cuota_fi
        WHERE fecha BETWEEN :start AND :period GROUP BY run_fondo
    )
    SELECT v.run_fondo run, v.fecha date,
        CASE WHEN COUNT(DISTINCT {_currency('v.moneda')})=1
                  AND COUNT({_currency('v.moneda')})=COUNT(*)
             THEN MIN({_currency('v.moneda')}) END currency,
        CASE WHEN BOOL_AND(v.patrimonio_neto IS NOT NULL AND v.patrimonio_neto>=0)
             THEN SUM(v.patrimonio_neto)::double precision END aum
    FROM latest l JOIN valores_cuota_fi v USING (run_fondo,fecha)
    GROUP BY v.run_fondo,v.fecha""", {'start': period-timedelta(days=max_age), 'period': period})
    return {r['run']: {**r, 'aum': float(r['aum']) if r['aum'] is not None and isfinite(float(r['aum'])) else None} for r in data}


def concentration(records):
    groups = defaultdict(list)
    for r in records:
        groups[r['run']].append(r)
    result = []
    for run, group in groups.items():
        ordered = sorted(group, key=lambda r: (r['weight'] is None, -(r['weight'] or 0)))
        valid = all(r['weight'] is not None for r in ordered)
        total = sum(r['weight'] for r in ordered) if valid else None
        valid = valid and total <= 100.01
        result.append(dict(run=run, fund_name=group[0]['fund_name'], admin=group[0]['admin'],
                           holders=len(group), top1=ordered[0]['weight'] if valid else None,
                           top5=sum(r['weight'] for r in ordered[:5]) if valid else None,
                           reported_weight=total if valid else None,
                           largest=ordered[0]['name'] if valid else None,
                           largest_rut=ordered[0].get('rut') if valid else None,
                           invalid_weights=not valid))
    return sorted(result, key=lambda r: (r['top1'] is None, -(r['top1'] or 0), r['run']))


BASE = """SELECT a.periodo, a.run_fondo run, NULLIF(TRIM(a.rut),'') rut,
COALESCE(a.nombre_canonical,a.nombre,'Sin nombre informado') name,
a.tipo_persona person_type, a.pct_propiedad weight, a.rank,
COALESCE(f.razon_social,a.run_fondo) fund_name, f.administrador admin
FROM aportantes_fi a LEFT JOIN fondos_inversion f USING(run_fondo) """


@lru_cache(maxsize=2)
def periods_for(bucket):
    from .router import rows
    return [r['periodo'] for r in rows('SELECT DISTINCT periodo FROM aportantes_fi WHERE periodo<=CURRENT_DATE ORDER BY periodo DESC')]


@lru_cache(maxsize=16)
def snapshot(period, bucket):
    from .router import rows
    return positions(rows(BASE + 'WHERE a.periodo=:period ORDER BY a.rank', {'period': period}))


def selected_period(period):
    bucket = int(time() // 60)
    periods = periods_for(bucket)
    selected = period or (periods[0] if periods else None)
    if selected and selected not in periods:
        raise HTTPException(422, 'Seleccione un trimestre disponible.')
    return selected, periods, bucket


@router.get('/ownership')
def ownership(period: date | None = None, view: Literal['directory', 'movements', 'concentration'] = 'directory',
              admin: str = Query('', max_length=200), person: Literal['', 'J', 'N'] = '',
              search: str = Query('', max_length=150), state: str = Query('', max_length=30),
              currency: str = Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'),
              nav_days: int = Query(31, ge=7, le=31),
              sort: Literal['aum', 'funds', 'admins', 'max_weight'] = 'aum', direction: Literal['asc', 'desc'] = 'desc',
              btg: Literal['', 'with', 'without'] = '', offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    if nav_days not in (7, 31):
        raise HTTPException(422, 'Seleccione una antigüedad máxima de 7 o 31 días.')
    selected, periods, bucket = selected_period(period)
    previous = shift(selected, -3) if selected else None
    current = snapshot(selected, bucket) if selected else []
    prior = snapshot(previous, bucket) if previous else []
    admins = sorted({r['admin'] for r in current + prior if r['admin']})
    scope = lambda records: [r for r in records if (not admin or r['admin'] == admin) and
                            (view == 'concentration' or not person or r['person_type'] == person)]
    scoped = scope(current)
    # Derive report presence before holder-type filters, which can hide an entire filing.
    movement = scope(changes(current, prior))
    query = normalize(search)
    navs = nav_snapshot(selected, bucket, nav_days) if selected and view == 'directory' else {}
    currencies = sorted({n['currency'] for n in navs.values() if n['currency']})
    if view == 'directory':
        data = directory(scoped)
        groups = defaultdict(list)
        for r in scoped:
            groups[r['rut']].append(r)
        for entry in data:
            entry.update(value_positions(groups[entry['rut']], navs, currency))
        btg_counts = {r['rut']: r['btg_funds'] for r in directory(current)}
        for entry in data:
            entry['btg_funds'] = btg_counts.get(entry['rut'], 0)
        data = [r for r in data if (btg != 'with' or r['btg_funds']) and (btg != 'without' or not r['btg_funds'])]
        data = [r for r in data if query in normalize(f"{r['name']} {r['rut']}")]
    elif view == 'movements':
        data = [r for r in movement if (not state or r['state'] == state) and query in normalize(f"{r['name']} {r['rut']} {r['fund_name']}")]
        data.sort(key=lambda r: (r['delta'] is None, -abs(r['delta'] or 0), r['name'] or '', r['run']))
    else:
        data = [r for r in concentration(scoped) if query in normalize(f"{r['fund_name']} {r['largest']} {r['run']}")]
    if view == 'directory':
        data = sort_directory(data, sort, direction)
    current_reports = {r['run'] for r in current if not admin or r['admin'] == admin}
    prior_reports = {r['run'] for r in prior if not admin or r['admin'] == admin}
    return dict(period=selected, previous=previous, periods=periods, admins=admins, currency=currency, currencies=currencies, nav_days=nav_days, sort=sort, direction=direction, total=len(data), rows=data[offset:offset + limit],
                summary=dict(owners=len({r['rut'] for r in scoped if r['rut']}), funds=len({r['run'] for r in scoped}),
                             admins=len({r['admin'] for r in scoped if r['admin']}), positions=len(scoped),
                             matched_reports=len(current_reports & prior_reports), current_reports=len(current_reports),
                             previous_reports=len(prior_reports)),
                movements={key: sum(r['state'] == key for r in movement) for key in ['appears', 'unlisted', 'increased', 'decreased', 'stable', 'no_report', 'unknown']})


@router.get('/ownership/{rut}')
def ownership_profile(rut: str, period: date | None = None, currency: str = Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'), nav_days: int = Query(31, ge=7, le=31)):
    from .router import rows
    if nav_days not in (7, 31):
        raise HTTPException(422, 'Seleccione una antigüedad máxima de 7 o 31 días.')
    selected, periods, bucket = selected_period(period)
    if not selected:
        return dict(period=None, periods=[], dates=[], rows=[], evolution=[], name=rut, rut=rut, admins=[])
    dates = [shift(selected, -3 * i) for i in reversed(range(8))]
    records = rows(BASE + 'WHERE a.rut=:rut AND a.periodo>=:start AND a.periodo<=:end ORDER BY a.periodo DESC,a.rank',
                   {'rut': rut, 'start': dates[0], 'end': selected})
    by_date = {d: positions([r for r in records if r['periodo'] == d]) for d in dates}
    # Report coverage must include all fund holders, not just the selected entity.
    coverage = rows('SELECT DISTINCT run_fondo run,periodo FROM aportantes_fi WHERE periodo>=:start AND periodo<=:end AND run_fondo IN (SELECT DISTINCT run_fondo FROM aportantes_fi WHERE rut=:rut AND periodo>=:start AND periodo<=:end)',
                    {'rut': rut, 'start': dates[0], 'end': selected})
    reports = {(r['run'], r['periodo']) for r in coverage}
    identities = {r['run']: r for r in reversed(records)}
    maps = {d: {r['run']: r for r in values} for d, values in by_date.items()}
    items = []
    for run, r in identities.items():
        weights = [maps[d].get(run, {}).get('weight') for d in dates]
        present = [run in maps[d] for d in dates]
        report = [(run, d) in reports for d in dates]
        delta = weights[-1] - weights[-2] if weights[-1] is not None and weights[-2] is not None else None
        items.append(dict(run=run, fund_name=r['fund_name'], admin=r['admin'], weights=weights,
                          present=present, reported=report, delta=delta))
    items.sort(key=lambda r: (not r['present'][-1], r['weights'][-1] is None, -(r['weights'][-1] or 0), r['run']))
    current = by_date[selected]
    valuation = value_positions([r for r in snapshot(selected,bucket) if r.get('rut')==rut], nav_snapshot(selected, bucket, nav_days), currency)
    admins = sorted({r['admin'] for r in current if r['admin']})
    return dict(period=selected, periods=periods, dates=dates, valuation=valuation, nav_days=nav_days, rut=rut, name=records[0]['name'] if records else rut,
                rows=items, admins=[dict(name=a, funds=len({r['run'] for r in current if r['admin'] == a})) for a in admins],
                evolution=[dict(period=d, funds=len(by_date[d]), admins=len({r['admin'] for r in by_date[d] if r['admin']})) for d in dates])
