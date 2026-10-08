"""Issuer/instrument history from reported national and foreign positions."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import json
from math import isfinite
import re
from time import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from .currency_exposure import end_month, shift, normalize

router = APIRouter()


def clean(value):
    value = ' '.join(str(value or '').split())
    return '' if value.upper() in ('', '0', 'NAN', 'NONE', 'NULL') else value


def identity(position):
    rut = re.sub(r'[.\s-]', '', clean(position.get('issuer_rut'))).upper()
    name = clean(position.get('issuer_name'))
    country = clean(position.get('country_code')).upper()
    identifier = clean(position.get('identifier'))
    instrument = clean(position.get('instrument_type'))
    currency = normalize(position.get('currency_code'))
    # Do not fuzzy-merge legal entities or treat unidentified issuers as one entity.
    issuer = ['rut', rut] if rut else ['name', country, name.upper()] if name else [
        'unknown', position['source'], identifier, instrument]
    issuer_id = json.dumps(issuer, ensure_ascii=False)
    key = json.dumps([issuer, identifier.upper(), instrument, currency,
                      clean(position.get('currency_code')) if currency == 'UNKNOWN' else ''], ensure_ascii=False)
    return issuer_id, key, bool(rut or name), currency


def valid_weight(value):
    try:
        value = float(value)
        return value if isfinite(value) and value >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_history(records, periods, dates):
    """Keep absent reports null; infer absence only in observed source tables."""
    coverage = {d: dict(period=d, sources=set(), rows=0, invalid_weights=0,
                       reported_weight=0., invalid_quantities=0, report_dates=set()) for d in dates}
    instruments = {}
    values = defaultdict(list)
    quantities = defaultdict(list)
    for p in sorted(records, key=lambda p: p['periodo']):
        period = end_month(p['periodo'])
        if period not in coverage:
            continue
        issuer_id, key, identified, currency = identity(p)
        instrument = instruments.setdefault(key, dict(
            id=key, issuer_id=issuer_id, issuer_identified=identified,
            sources=set(), weights=[], present=[], quantities=[], quantity_units=[], report_dates=[]))
        instrument.update({field: p.get(field) for field in (
            'kind', 'run', 'periodo', 'identifier', 'instrument_type', 'instrument_name',
            'issuer', 'issuer_rut', 'country', 'fund_name', 'admin')})
        instrument.update(currency=currency, currency_code=p.get('currency_code'))
        instrument['sources'].add(p['source'])
        weight = valid_weight(p['weight'])
        values[(key, period)].append(weight)
        quantity = valid_weight(p.get('quantity'))
        unit = clean(p.get('quantity_unit')).upper() or None
        quantities[(key, period)].append((quantity, unit, p['periodo']))
        c = coverage[period]
        c['sources'].add(p['source'])
        c['report_dates'].add(p['periodo'])
        c['rows'] += 1
        c['invalid_weights'] += int(weight is None)
        c['invalid_quantities'] += int(quantity is None or unit is None)
        c['reported_weight'] += weight or 0.
    for key, item in instruments.items():
        for d in dates:
            weights = values.get((key, d))
            observed = item['sources'].issubset(coverage[d]['sources'])
            # A missing source can conceal part of a multi-source holding.
            weight = (sum(weights) if weights and None not in weights else 0. if not weights else None) if observed else None
            item['weights'].append(weight)
            item['present'].append(bool(weights))
            reported = quantities.get((key, d), [])
            units = {unit for _, unit, _ in reported}
            unit = next(iter(units)) if len(units) == 1 else None
            # Never add unlike unit types. Unidentified groups may contain
            # different securities, so their quantities cannot be aggregated.
            comparable = unit is not None and all(q is not None for q, _, _ in reported)
            identified = bool(clean(item['identifier']))
            quantity = (sum(q for q, _, _ in reported) if comparable and (identified or len(reported) == 1)
                        else None) if reported else 0.
            item['quantities'].append(quantity if observed else None)
            item['quantity_units'].append(unit)
            item['report_dates'].append(sorted({d for _, _, d in reported}))
        item['sources'] = sorted(item['sources'])
    for c in coverage.values():
        c['sources'] = sorted(c['sources'])
        c['report_dates'] = sorted(c['report_dates'])
        if not c['rows']:
            c['reported_weight'] = None
    return dict(periods=periods, dates=dates, coverage=list(coverage.values()), instruments=list(instruments.values()))


@lru_cache(maxsize=32)
def load_history(kind, run, period, months, bucket):
    from .router import rows, SOURCES, ENRICH
    sources = [s for s in SOURCES if s[0] == kind]
    # Source dates may use month-start labels. Keep the most recent filing per
    # source/month, and expose those actual dates alongside the month-end axis.
    periods = rows(' UNION '.join(
        f"SELECT DISTINCT (date_trunc('month',periodo)+interval '1 month - 1 day')::date period "
        f"FROM {table} WHERE run_fondo=:run AND periodo<=CURRENT_DATE"
        for _, table, *_ in sources) + ' ORDER BY period DESC', {'run': run})
    choices = [p['period'] for p in periods if p['period'] <= date.today()]
    selected = period or (choices[0] if choices else None)
    if selected and selected not in choices:
        raise HTTPException(422, 'Seleccione un período de cartera disponible.')
    if not selected:
        return dict(kind=kind, run=run, period=None, **build_history([], [], []))
    step = 1 if kind == 'fm' else 3
    count = max(1, (months + step - 1) // step)
    # Extra opening period supplies the first visible column's change.
    dates = [shift(selected, -step * i) for i in reversed(range(count + 1))]
    parts = []
    numeric = r'^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
    for k, table, identifier, rut, issuer, weight in sources:
        currency = 'moneda_liquidacion' if k == 'fm' else 'cod_moneda_liquidacion'
        country = 'codigo_pais_emisor' if k == 'fm' else 'cod_pais'
        quantity = 'cantidad_unidades' if k == 'fm' else 'cant_unidades'
        parts.append(f"""SELECT '{k}' kind, '{table}' source, c.run_fondo run,
            c.periodo, {identifier} identifier, {rut} issuer_rut, {issuer} issuer_name,
            tipo_instrumento instrument_type, {country} country_code, {currency} currency_code,
            CASE WHEN trim(c.{weight}::text) ~ :numeric THEN c.{weight}::numeric END weight,
            CASE WHEN trim(c.{quantity}::text) ~ :numeric THEN c.{quantity}::numeric END quantity,
            c.tipo_unidades quantity_unit
            FROM {table} c JOIN (
                SELECT date_trunc('month',periodo) AS report_month, max(periodo) periodo FROM {table}
                WHERE run_fondo=:run AND periodo BETWEEN :start AND :end GROUP BY 1
            ) latest ON c.periodo=latest.periodo
            WHERE c.run_fondo=:run AND c.periodo BETWEEN :start AND :end""")
    records = rows('WITH p AS (' + ' UNION ALL '.join(parts) + ') ' + ENRICH,
                   dict(run=run, start=dates[0].replace(day=1), end=selected, numeric=numeric))
    return dict(kind=kind, run=run, period=selected, **build_history(records, choices, dates))


@router.get('/portfolio-history/{kind}/{run}')
def portfolio_history(kind: Literal['fm', 'fi'], run: str,
                      period: date | None = None, months: int = Query(12, ge=3, le=60)):
    return load_history(kind, run, period, months, int(time() // 60))
