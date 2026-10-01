"""Reported portfolio currencies. No FX or derivative netting."""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from math import isfinite
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from src.db.engine import SessionLocal

router = APIRouter(prefix='/currency-exposure')
SOURCES = [('fm', 'cartera_naci', 'moneda_liquidacion', 'porcentaje_activos_fondo'),
           ('fm', 'cartera_extr', 'moneda_liquidacion', 'porcentaje_activos_fondo'),
           ('fi', 'cartera_fi_nac', 'cod_moneda_liquidacion', 'pct_activo_fondo'),
           ('fi', 'cartera_fi_ext', 'cod_moneda_liquidacion', 'pct_activo_fondo')]
# CMF codes are not all ISO: USD means Unidad Seguro Dólar; PROM is US dollars.
ALIASES = {'$$': 'CLP', '$': 'CLP', 'CLP': 'CLP', 'UF': 'UF', 'CLF': 'UF',
           'PROM': 'USD', 'US$': 'USD', 'USD': 'USD_SEGURO', 'DA': 'AUD',
           'YY': 'JPY', 'CD': 'DKK', 'CS': 'SEK', 'FS': 'CHF', 'UYP': 'UYU'}
CURRENCIES = set('EUR GBP CAD AUD JPY DKK SEK CHF UYU PEN COP BRL ARS MXN CNY HKD NZD NOK SGD ZAR BOB INR KRW PLN CZK TRY TWD ILS IDR MYR PHP THB CRC DOP GTQ UTM UDI UVR IVP ECU'.split())


def read(sql, params=None):
    with SessionLocal() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        s.execute(text("SET LOCAL statement_timeout='20s'"))
        return [dict(r) for r in s.execute(text(sql), params or {}).mappings()]


def normalize(code):
    value = str(code or '').strip().upper()
    return ALIASES.get(value, value if value in CURRENCIES else 'UNKNOWN')


def end_month(d):
    return d.replace(day=monthrange(d.year, d.month)[1])


def shift(d, months):
    n = d.year * 12 + d.month - 1 + months
    return end_month(date(n // 12, n % 12 + 1, 1))


def period_choices(kind, run=None):
    parts = []
    for k, table, *_ in SOURCES:
        if kind != 'all' and k != kind:
            continue
        parts.append(f"SELECT '{k}' kind, periodo FROM {table} WHERE periodo<=CURRENT_DATE"
                     + (' AND run_fondo=:run' if run else '') + ' GROUP BY periodo')
    records = read(' UNION '.join(parts), {'run': run})
    available = defaultdict(set)
    for r in records:
        if end_month(r['periodo']) <= date.today():
            available[r['kind']].add(end_month(r['periodo']))
    dates = (available['fm'] & available['fi']) if kind == 'all' else available[kind]
    return sorted(dates, reverse=True)


def reported_positions(kind, start, end, run=None):
    parts = []
    numeric = "^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"
    for k, table, column, weight in SOURCES:
        if kind != 'all' and k != kind:
            continue
        # Normalize FM month-start labels to month end, never carry forward.
        parts.append(f"""SELECT '{k}' kind, '{table}' source, run_fondo run,
            (date_trunc('month',periodo)+interval '1 month - 1 day')::date period,
            {column} code, CASE WHEN trim({weight}::text) ~ :numeric THEN {weight}::numeric END weight
            FROM {table} WHERE periodo>=:start AND periodo<=:end
            {'AND run_fondo=:run' if run else ''}""")
    return read('WITH p AS (' + ' UNION ALL '.join(parts) + ''')
        SELECT kind,source,run,period,code,count(*) positions,
               count(*) FILTER (WHERE weight IS NULL OR weight<0 OR weight>100) invalid,
               SUM(weight) FILTER (WHERE weight>=0 AND weight<=100) weight
        FROM p GROUP BY kind,source,run,period,code''',
        {'start': start.replace(day=1), 'end': end, 'run': run, 'numeric': numeric})


def summarize(records):
    if not records:
        return None
    buckets = defaultdict(float)
    codes = defaultdict(set)
    invalid = 0
    for r in records:
        code = normalize(r['code'])
        invalid += r['invalid']
        w = float(r['weight']) if r['weight'] is not None else 0.
        if not isfinite(w):
            invalid += 1
            continue
        buckets[code] += w
        codes[code].add(str(r['code'] or '(vacío)'))
    total = sum(buckets.values())
    valid = invalid == 0 and 0 < total <= 100.5
    return dict(positions=sum(r['positions'] for r in records), invalid_weights=invalid,
                sources=sorted({r['source'] for r in records}), reported_weight=total,
                known_weight=total-buckets.get('UNKNOWN', 0), unknown_weight=buckets.get('UNKNOWN', 0),
                unreported_weight=max(0., 100-total), valid=valid,
                uf=buckets.get('UF', 0.) if valid else None,
                buckets=[dict(currency=c, weight=w, codes=sorted(codes[c]))
                         for c,w in sorted(buckets.items(), key=lambda item:-item[1])])


def group_positions(records):
    grouped = defaultdict(list)
    for r in records:
        grouped[(r['kind'], r['run'], r['period'])].append(r)
    return {key: summarize(items) for key,items in grouped.items()}


def weighted_sample(funds):
    eligible_aum = sum(f['aum'] for f in funds)
    sample = [f for f in funds if f.get('exposure') and f['exposure']['valid']]
    sample_aum = sum(f['aum'] for f in sample)
    buckets = defaultdict(float)
    for f in sample:
        for b in f['exposure']['buckets']:
            buckets[b['currency']] += f['aum'] * b['weight'] / sample_aum
    dates = [f['nav_date'] for f in sample]
    return dict(eligible_funds=len(funds), reported_funds=sum(bool(f.get('exposure')) for f in funds),
                sample_funds=len(sample), excluded_funds=sum(bool(f.get('exposure')) and not f['exposure']['valid'] for f in funds),
                eligible_aum=eligible_aum, sample_aum=sample_aum,
                aum_coverage=sample_aum/eligible_aum*100 if eligible_aum>0 else None,
                nav_from=min(dates) if dates else None, nav_to=max(dates) if dates else None,
                uf=buckets.get('UF', 0.) if sample else None,
                reported_weight=sum(buckets.values()) if sample else None,
                known_weight=sum(w for c,w in buckets.items() if c!='UNKNOWN') if sample else None,
                buckets=[dict(currency=c, weight=w) for c,w in sorted(buckets.items(),key=lambda item:-item[1])])


def nav_universe(kind, currency, start, end, admin, run=None):
    parts = []
    for k, table, name, manager, active in [
        ('fm','fondo_mutuo','nombre_fondo','razon_social_administradora','f.fecha_termino_operaciones IS NULL'),
        ('fi','fondos_inversion','razon_social','administrador','f.vigente IS TRUE')]:
        if kind!='all' and k!=kind:
            continue
        parts.append(f"""SELECT '{k}' kind,m.run_fondo run,f.{name} name,f.{manager} admin,
            (m.month+interval '1 month - 1 day')::date period,m.aum,m.latest_data_date nav_date,
            c.tipo category_type,c.categoria category_code,COALESCE(c.nombre_cat,'Sin clasificación') category
            FROM mv_industry_monthly_{k} m JOIN {table} f USING(run_fondo)
            LEFT JOIN LATERAL (SELECT * FROM categoria_{k} c WHERE c.run_fondo=m.run_fondo
                AND c.periodo <= (m.month+interval '1 month - 1 day')::date ORDER BY c.periodo DESC LIMIT 1) c ON true
            WHERE m.month>=:start AND m.month<=:end AND m.currency=:currency
              AND m.aum>0 AND m.aum::text NOT IN ('NaN','Infinity','-Infinity') AND {active}
              AND (:admin IS NULL OR f.{manager}=:admin) {'AND m.run_fondo=:run' if run else ''}""")
    return read(' UNION ALL '.join(parts), {'start': start.replace(day=1), 'end': end,
                                          'currency': currency, 'admin': admin, 'run': run})


def choose(period, choices):
    if period and period not in choices:
        raise HTTPException(422, 'Seleccione una fecha de cartera disponible.')
    return period or (choices[0] if choices else None)


@router.get('')
def industry_exposure(currency: str=Query('CLP',pattern='^(?:[A-Z]{3}|UF)$'),
                      kind: Literal['all','fm','fi']='all', period: date|None=None,
                      sector: Literal['debt','all','private_debt']='debt',
                      admin: str|None=None, category: str|None=None):
    choices = period_choices(kind)
    selected = choose(period, choices)
    if not selected:
        return dict(periods=[], period=None, history=[], funds=[], strategies=[], summary=weighted_sample([]), categories=[])
    step = 1 if kind=='fm' else 3
    periods = [shift(selected,-step*i) for i in reversed(range(8))]
    positions = group_positions(reported_positions(kind,periods[0],selected))
    nav = nav_universe(kind,currency,periods[0],selected,admin)
    eligible = [f for f in nav if f['period'] in periods and
                (sector=='all' or sector=='debt' and (f['category_type']=='Deuda' or f['category_code']=='FI_DEUDA_PRIVADA')
                 or sector=='private_debt' and f['category_code']=='FI_DEUDA_PRIVADA')]
    categories = sorted({f['category'] for f in eligible if f['period']==selected})
    grouped = defaultdict(list)
    for f in eligible:
        if category and f['category']!=category:
            continue
        f['aum'] = float(f['aum'])
        f['exposure'] = positions.get((f['kind'],f['run'],f['period']))
        previous = positions.get((f['kind'],f['run'],shift(f['period'],-step)))
        f['uf_change'] = (f['exposure']['uf']-previous['uf'] if f['exposure'] and f['exposure']['valid'] and previous and previous['valid'] else None)
        grouped[f['period']].append(f)
    current = grouped[selected]
    strategies = []
    for name in sorted({f['category'] for f in current}):
        strategies.append(dict(category=name, **weighted_sample([f for f in current if f['category']==name])))
    return dict(currency=currency, periods=choices, period=selected, categories=categories,
                summary=weighted_sample(current), strategies=strategies,
                history=[dict(period=p, **weighted_sample(grouped[p])) for p in periods],
                funds=sorted(current,key=lambda f:(-(f['exposure']['uf'] if f['exposure'] and f['exposure']['uf'] is not None else -1), f['name'])))


@router.get('/fund/{kind}/{run}')
def fund_exposure(kind: Literal['fm','fi'], run: str, period: date|None=None):
    choices=period_choices(kind,run)
    selected=choose(period,choices)
    if not selected:
        return dict(periods=[],period=None,summary=None,history=[])
    step=1 if kind=='fm' else 3
    periods=[shift(selected,-step*i) for i in reversed(range(8))]
    positions=group_positions(reported_positions(kind,periods[0],selected,run))
    return dict(periods=choices, period=selected, summary=positions.get((kind,run,selected)),
                history=[dict(period=p,uf=(positions.get((kind,run,p)) or {}).get('uf')) for p in periods])
