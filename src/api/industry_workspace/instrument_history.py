"""Batched holder histories for an exact instrument/type/issuer selection."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import json
from time import time

from fastapi import APIRouter, Query

from .currency_exposure import end_month, shift, normalize
from .portfolio_history import clean, valid_weight

router = APIRouter()
NUMERIC = r'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'


def build_groups(records, reports, axes):
    # Source filing presence comes from all positions in that fund's report,
    # including months in which the selected instrument is absent.
    filings = {(r['kind'], r['run'], r['source'], end_month(r['periodo'])): r['periodo'] for r in reports}
    holders = {}
    values = defaultdict(list)
    for p in sorted(records, key=lambda r: r['periodo']):
        period = end_month(p['periodo'])
        if period not in axes[p['kind']]:
            continue
        if filings.get((p['kind'], p['run'], p['source'], period)) != p['periodo']:
            continue  # An earlier filing within this month has been superseded.
        currency = normalize(p['currency_code'])
        key = (p['kind'], p['run'], currency, clean(p['currency_code']) if currency == 'UNKNOWN' else '')
        h = holders.setdefault(key, dict(id=json.dumps(key), kind=p['kind'], run=p['run'],
            currency=currency, sources=set(), weights=[], quantities=[], quantity_units=[], present=[], report_dates=[]))
        h.update(fund_name=p.get('fund_name') or p['run'], admin=p.get('admin') or 'Sin administradora',
                 identifier=p['identifier'])
        h['sources'].add(p['source'])
        values[key, period].append(p)
    for key, h in holders.items():
        for period in axes[h['kind']]:
            positions = values.get((key, period), [])
            observed_dates = [filings.get((h['kind'], h['run'], source, period)) for source in h['sources']]
            observed = all(observed_dates)
            weights = [valid_weight(p['weight']) for p in positions]
            quantities = [valid_weight(p['quantity']) for p in positions]
            units = {clean(p['quantity_unit']).upper() or None for p in positions}
            unit = next(iter(units)) if len(units) == 1 else None
            weight = sum(weights) if None not in weights else None
            quantity = (sum(quantities) if unit and None not in quantities else None) if positions else 0.
            h['weights'].append(weight if observed else None)
            h['quantities'].append(quantity if observed else None)
            h['quantity_units'].append(unit)
            h['present'].append(bool(positions))
            h['report_dates'].append(sorted({d for d in observed_dates if d}))
        h['sources'] = sorted(h['sources'])
    return [dict(kind=kind, period=dates[-1] if dates else None, dates=dates,
                 rows=sorted([h for h in holders.values() if h['kind'] == kind],
                             key=lambda h: (not h['present'][-1], -(h['weights'][-1] or 0), h['fund_name'], h['id'])))
            for kind, dates in axes.items()]


@lru_cache(maxsize=32)
def load_history(identifier, instrument_type, issuer, months, bucket):
    from .router import rows, SOURCES, ENRICH
    # Anchor each reporting frequency to its latest complete calendar period.
    today = date.today()
    completed = today if end_month(today) == today else shift(today, -1)
    cutoffs = rows(' UNION ALL '.join(
        f"SELECT '{kind}' kind, MAX(periodo) periodo FROM {table} WHERE periodo<=:end"
        for kind, table, *_ in SOURCES), {'end': completed})
    latest = {}
    for r in cutoffs:
        if r['periodo']:
            p = end_month(r['periodo'])
            latest[r['kind']] = max(latest.get(r['kind'], p), p)
    axes = {}
    for kind in ['fm', 'fi']:
        step = 1 if kind == 'fm' else 3
        count = (months + step - 1) // step
        axes[kind] = [shift(latest[kind], -step*i) for i in reversed(range(count+1))] if kind in latest else []
    parts = []
    params = dict(identifier=identifier, instrument_type=instrument_type, issuer=issuer, numeric=NUMERIC)
    for kind, table, ident, rut, issuer_name, weight in SOURCES:
        if not axes[kind]:
            continue
        params[f'{kind}_start'] = axes[kind][0].replace(day=1)
        params[f'{kind}_end'] = axes[kind][-1]
        quantity = 'cantidad_unidades' if kind == 'fm' else 'cant_unidades'
        currency = 'moneda_liquidacion' if kind == 'fm' else 'cod_moneda_liquidacion'
        country = 'codigo_pais_emisor' if kind == 'fm' else 'cod_pais'
        parts.append(f"""SELECT '{kind}' kind,'{table}' source,c.run_fondo run,c.periodo,
            {ident} identifier,{rut} issuer_rut,{issuer_name} issuer_name,
            tipo_instrumento instrument_type,{country} country_code,{currency} currency_code,
            CASE WHEN trim(c.{weight}::text) ~ :numeric THEN c.{weight}::numeric END weight,
            CASE WHEN trim(c.{quantity}::text) ~ :numeric THEN c.{quantity}::numeric END quantity,
            tipo_unidades quantity_unit FROM {table} c
            WHERE periodo BETWEEN :{kind}_start AND :{kind}_end
              AND {ident}=:identifier AND tipo_instrumento IS NOT DISTINCT FROM :instrument_type""")
    # Materialize the small matching-instrument set before enriching names;
    # otherwise the planner can push large dimension joins into source scans.
    records = rows('WITH p AS MATERIALIZED ('+' UNION ALL '.join(parts)+'), enriched AS ('+ENRICH+
                   ') SELECT * FROM enriched WHERE issuer=:issuer', params) if parts else []
    # A single additional query checks filing presence for every historical holder.
    coverage = []
    for kind, table, *_ in SOURCES:
        runs = sorted({r['run'] for r in records if r['source'] == table})
        if not runs:
            continue
        params[f'{table}_runs'] = runs
        coverage.append(f"""SELECT '{kind}' kind,'{table}' source,run_fondo run,MAX(periodo) periodo
            FROM {table} WHERE run_fondo=ANY(:{table}_runs)
            AND periodo BETWEEN :{kind}_start AND :{kind}_end
            GROUP BY run_fondo,date_trunc('month',periodo)""")
    reports = rows(' UNION ALL '.join(coverage), params) if coverage else []
    return dict(identifier=identifier, instrument_type=instrument_type, issuer=issuer,
                groups=build_groups(records, reports, axes))


@router.get('/instrument-history')
def instrument_history(identifier: str = Query(min_length=1, max_length=150),
                       issuer: str = Query(max_length=500),
                       instrument_type: str | None = Query(None, max_length=50),
                       months: int = Query(12, ge=3, le=60)):
    return load_history(identifier, instrument_type, issuer, months, int(time()//60))
