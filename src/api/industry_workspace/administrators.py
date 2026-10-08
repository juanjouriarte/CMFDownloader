"""Read-only monthly administrator workspace for the industry workspace."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
from src.api.classification_admin import classification_cache_epoch
from typing import Literal
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/administrators')


@lru_cache(maxsize=64)
def load_currencies(admin, kind, period, category, cache_bucket):
    """NAV-backed currencies in this month, plus per-type history for the NNM matrix."""
    from .router import rows
    parts = []
    for k, table, admin_column, active in [
        ('fm', 'fondo_mutuo', 'razon_social_administradora', 'f.fecha_termino_operaciones IS NULL'),
        ('fi', 'fondos_inversion', 'administrador', 'f.vigente IS TRUE'),
    ]:
        if kind not in ('all', k):
            continue
        parts.append(f"""SELECT '{k}' kind, m.currency,
            BOOL_OR(m.month=:period) current_month
            FROM mv_industry_monthly_{k} m JOIN {table} f USING(run_fondo)
            LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo, nombre_cat
                FROM categoria_{k}_effective WHERE periodo<=CURRENT_DATE
                ORDER BY run_fondo, periodo DESC) c USING(run_fondo)
            WHERE COALESCE(f.{admin_column},'Sin administradora')=:admin AND {active}
                AND m.month BETWEEN :start AND :period
                AND m.currency ~ '^(?:[A-Z]{{3}}|UF)$'
                AND m.aum IS NOT NULL AND m.aum>=0
                AND m.aum::text NOT IN ('NaN','Infinity','-Infinity')
                AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)
            GROUP BY m.currency""")
    data = rows(' UNION ALL '.join(parts), dict(admin=admin, period=period,
        start=max(date(2020, 1, 1), month_shift(period, -60)), category=category))
    return dict(currencies=sorted({r['currency'] for r in data if r['current_month']}),
        flow_currencies=sorted({r['currency'] for r in data if r['kind']=='fm'}),
        flow_currencies_by_kind={k:sorted({r['currency'] for r in data if r['kind']==k}) for k in ('fm','fi')})


@router.get('/currencies')
def administrator_currencies(admin: str, period: date,
        kind: Literal['all','fm','fi']='all', category: str|None=None):
    period = period.replace(day=1)
    if period > date.today() or period < date(2020, 1, 1):
        raise HTTPException(422, 'Seleccione un mes entre 2020 y el actual.')
    return load_currencies(admin, kind, period, category, classification_cache_epoch())


def month_shift(value, offset):
    n = value.year * 12 + value.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


def build_analysis(data, selected, currency, kind, category=None):
    from .router import market_snapshots
    categories = sorted({r['category'] for r in data if r['month'] == selected})
    chosen = [r for r in data if not category or r['category'] == category]
    snapshots = market_snapshots(chosen)
    by_month = {s['month']: s for s in snapshots}
    # Emit missing months explicitly: never substitute an older observation.
    history = []
    for i in range(-12, 1):
        month = month_shift(selected, i)
        snap = by_month.get(month)
        history.append(snap or dict(month=month, date=None, market_aum=None,
            reported_funds=0, eligible_funds=0, coverage_pct=None, administrators=[]))
    for snap in history:
        ordered = sorted(snap['administrators'], key=lambda a: (-a['aum'], a['admin']))
        for a in ordered:
            a['rank'] = 1 + sum(b['aum'] > a['aum'] for b in ordered)
        snap['administrators'] = ordered
    current = history[-1]
    previous, year = history[-2], history[0]
    previous_admins = {a['admin']: a for a in previous['administrators']}
    year_admins = {a['admin']: a for a in year['administrators']}
    def growth(a, b):
        return (a / b - 1) * 100 if a is not None and b is not None and b > 0 else None
    current_funds = [r for r in chosen if r['month'] == selected and r['latest_data_date'] == current['date']]
    prior_funds = [r for r in chosen if r['month'] == previous['month'] and r['latest_data_date'] == previous['date']]
    prior_by_key = {(r['kind'], r['run_fondo']): r for r in prior_funds}
    current_keys = {(r['kind'], r['run_fondo']) for r in current_funds}
    fund_rows = []
    for f in current_funds:
        old = prior_by_key.get((f['kind'], f['run_fondo']))
        # Changes between funds in the same administrator; no synthetic zero base.
        comparable = old is not None and old['admin'] == f['admin']
        fund_rows.append(dict(kind=f['kind'], run=f['run_fondo'], name=f['fund_name'], admin=f['admin'],
            category=f['category'], aum=float(f['aum']), date=f['latest_data_date'],
            previous_aum=float(old['aum']) if comparable else None,
            change=float(f['aum'] - old['aum']) if comparable else None,
            status='comparable' if comparable else 'without_base'))
    for old in prior_funds:
        if (old['kind'], old['run_fondo']) not in current_keys:
            fund_rows.append(dict(kind=old['kind'], run=old['run_fondo'], name=old['fund_name'], admin=old['admin'],
                category=old['category'], aum=None, date=None, previous_aum=float(old['aum']),
                change=None, status='not_reported'))
    category_totals = defaultdict(float)
    for f in current_funds:
        category_totals[f['category']] += float(f['aum'])
    admins = []
    for a in current['administrators']:
        old, annual = previous_admins.get(a['admin']), year_admins.get(a['admin'])
        mix = [dict(**c, market_share=c['aum'] / category_totals[c['name']] * 100
                    if category_totals[c['name']] > 0 else None) for c in a['categories']]
        own = [f for f in fund_rows if f['admin'] == a['admin'] and f['aum'] is not None]
        admins.append(dict(**{k:v for k,v in a.items() if k != 'categories'}, categories=mix,
            mom=growth(a['aum'], old['aum'] if old else None),
            yoy=growth(a['aum'], annual['aum'] if annual else None),
            share_change=a['share'] - old['share'] if old and a['share'] is not None and old['share'] is not None else None,
            rank_change=old['rank'] - a['rank'] if old else None,
            fm_aum=sum(f['aum'] for f in own if f['kind'] == 'fm'),
            fi_aum=sum(f['aum'] for f in own if f['kind'] == 'fi'),
            fm_funds=sum(f['kind'] == 'fm' for f in own), fi_funds=sum(f['kind'] == 'fi' for f in own)))
    return dict(currency=currency, kind=kind, month=selected, date=current['date'], previous_date=previous['date'],
        year_date=year['date'], partial=selected == date.today().replace(day=1),
        market_aum=current['market_aum'], reported_funds=current['reported_funds'], eligible_funds=current['eligible_funds'],
        coverage_pct=current['coverage_pct'], administrators=admins, history=history,
        categories=categories, category_totals=[dict(name=k, aum=v) for k,v in sorted(category_totals.items(), key=lambda r:-r[1])],
        funds=sorted(fund_rows, key=lambda f: (f['aum'] is None, -(f['aum'] or 0), f['name'])))


@lru_cache(maxsize=32)
def load_analysis(currency, kind, period, category, cache_bucket):
    from .router import monthly_data, rows
    tables = [f'mv_industry_monthly_{k}' for k in ['fm', 'fi'] if kind == 'all' or kind == k]
    periods = [r['month'] for r in rows('SELECT DISTINCT month FROM (' + ' UNION ALL '.join(
        f"SELECT month FROM {table} WHERE currency=:currency AND aum IS NOT NULL AND month<=CURRENT_DATE AND month>='2020-01-01'" for table in tables
    ) + ') p ORDER BY month DESC', {'currency': currency})]
    selected = period or (periods[0] if periods else date.today().replace(day=1))
    data = monthly_data(currency, kind, month_shift(selected, -12), selected, active_only=True)
    return dict(**build_analysis(data, selected, currency, kind, category), periods=periods)


@router.get('/analysis')
def administrator_analysis(currency: str=Query('CLP', pattern='^(?:[A-Z]{3}|UF)$'),
        kind: Literal['all','fm','fi']='all', period: date|None=None, category: str|None=None):
    if period:
        period = period.replace(day=1)
        if period > date.today() or period < date(2020,1,1):
            raise HTTPException(422, 'Seleccione un mes entre 2020 y el actual.')
    return load_analysis(currency, kind, period, category, classification_cache_epoch())
