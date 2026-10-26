"""Monthly insurer fund positions, comparable changes, and dated fund NAV bases."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import calendar


def shift(month, offset):
    n = month.year * 12 + month.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


def month_end(month):
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def decimal(value):
    parsed = Decimal(str(value)) if value is not None else None
    return parsed if parsed is not None and parsed.is_finite() else None


def currency(value):
    value = (value or '').strip().upper()
    return {'$$': 'CLP', '$': 'CLP', 'PROM': 'USD', 'US$': 'USD', 'CLF': 'UF', '0': ''}.get(value, value)


def load_bases(read, holdings, periods):
    """No live FX or inferred portfolio AUM: retain native NAV denominations."""
    bases, owners = {}, defaultdict(dict)
    if not periods:
        return bases, owners
    for kind, table, registry, price in (
        ('fm', 'cartola_diaria', 'fondo_mutuo', 'valor_cuota'),
        ('fi', 'valores_cuota_fi', 'fondos_inversion', 'valor_libro'),
    ):
        runs = sorted({f['fund_run'] for f in holdings if f['fund_kind'] == kind and f['fund_run']})
        if not runs:
            continue
        params = dict(runs=runs, periods=periods, dates=[month_end(p) for p in periods])
        monthly = read(f"""SELECT run_fondo,month,currency,aum,latest_data_date
            FROM mv_industry_monthly_{kind} WHERE run_fondo=ANY(:runs) AND month=ANY(:periods)""", params)
        for m in monthly:
            key = (kind, m['run_fondo'], m['month'])
            base = bases.setdefault(key, dict(parts=[], series=[], complete=True))
            value = decimal(m['aum'])
            base['complete'] = base['complete'] and value is not None and value >= 0 and bool(currency(m['currency']))
            base['parts'].append(dict(currency=currency(m['currency']), value=value, date=m['latest_data_date']))
        nav = read(f"""SELECT v.run_fondo,v.fecha,v.serie,COALESCE(NULLIF(trim(v.moneda),''),f.moneda) currency,
                v.patrimonio_neto aum,v.{price} price
            FROM {table} v JOIN {registry} f USING(run_fondo)
            WHERE v.run_fondo=ANY(:runs) AND v.fecha=ANY(:dates)""", params)
        by_month = defaultdict(list)
        for r in nav:
            r['aum'], r['price'] = decimal(r['aum']), decimal(r['price'])
            by_month[r['run_fondo'], r['fecha'].replace(day=1)].append(r)
        for (run, period), rows in by_month.items():
            groups = defaultdict(list)
            for r in rows:
                r['currency'] = currency(r['currency'])
                groups[r['currency']].append(r)
            # Never reuse a stale series beside a current one. These are only
            # the series actually reported at the insurance month-end.
            parts = [dict(currency=c, value=(sum((r['aum'] for r in group if r['aum'] is not None), Decimal(0))
                                            if any(r['aum'] is not None for r in group) else None),
                          date=month_end(period)) for c, group in groups.items()]
            bases[kind, run, period] = dict(parts=parts, series=rows,
                complete=all(r['aum'] is not None and r['aum'] >= 0 and r['currency'] for r in rows))
        if kind == 'fi':
            reported = read("""SELECT run_fondo,periodo,rut,sum(pct_propiedad) share
                FROM aportantes_fi WHERE run_fondo=ANY(:runs) AND periodo=ANY(:dates)
                GROUP BY run_fondo,periodo,rut HAVING count(*)=count(pct_propiedad)""", params)
            for r in reported:
                if decimal(r['share']) is not None:
                    owners[kind, r['run_fondo'], r['periodo'].replace(day=1)][r['rut']] = r['share']
    return bases, owners


def fund_share(positions, base, reported, period, allow_reported):
    """Return source and reason with every percentage; never clamp a bad ratio."""
    if not positions:
        return None, None, 'no_position'
    holders = {p['insurer_rut'] for p in positions}
    if allow_reported and holders.issubset(reported):
        share = sum((decimal(reported[rut]) for rut in holders), Decimal(0))
        if 0 <= share <= 100:
            return share, 'reported_shareholders', None
    if not base or not base['parts']:
        return None, None, 'no_fund_nav'
    if not base['complete']:
        return None, None, 'incomplete_nav'
    if any(p['date'] != month_end(period) for p in base['parts']):
        return None, None, 'different_nav_date'
    parts = base['parts']
    if len(parts) != 1 or not parts[0]['currency']:
        return None, None, 'mixed_nav_currencies'
    total = decimal(parts[0]['value'])
    if total is None or total <= 0:
        return None, None, 'nonpositive_nav'
    series = {(r['serie'] or '').strip().upper(): r for r in base['series']}
    matched, native_value = bool(series) and all(p['share_positions'] for p in positions), Decimal(0)
    for p in positions:
        for q in p['share_positions']:
            nav = series.get((q['series'] or '').strip().upper())
            units = decimal(q['units'])
            if not nav or units is None or units < 0 or nav['price'] is None or nav['price'] <= 0:
                matched = False
            else:
                native_value += units * decimal(nav['price'])
    if matched:
        share, source = native_value / total * 100, 'units_nav_estimate'
    elif parts[0]['currency'] == 'CLP' and all(p['value_clp'] is not None and not p['unvalued_positions'] for p in positions):
        share = sum((decimal(p['value_clp']) for p in positions), Decimal(0)) / total * 100
        source = 'clp_valuation_estimate'
    else:
        return None, None, 'unmatched_series_or_currency'
    if not 0 <= share <= 100:
        return None, None, 'inconsistent_share'
    return share, source, None


def build_history(holdings, reports, available, start, end, bases, owners, allow_reported=True):
    all_months = [shift(start, i) for i in range((end.year-start.year)*12+end.month-start.month+1)]
    loaded = [p for p in all_months if p in available]
    # Retain internal gaps, but do not manufacture years of pre-import columns.
    dates = [p for p in all_months if loaded and loaded[0] <= p <= loaded[-1]]
    coverage = defaultdict(dict)
    for f in reports:
        coverage[f['period'], f['insurer_rut']][f['family']] = f['status']
    groups = defaultdict(list)
    for f in holdings:
        groups[f['fund_key']].append(f)
    output = []
    for key, records in groups.items():
        latest = max(records, key=lambda f: f['period'])
        kind, run = latest['fund_kind'], latest['fund_run']
        # Both domestic and foreign fund files must be usable before a missing
        # holding can mean zero, or a month-on-month delta can be calculated.
        families = {'F', 'X'} if kind == 'fm' else {'A', 'X'} if kind in {'fi', 'fip'} else {'X'}
        families = set(latest.get('coverage_families', families))
        cohort = {f['insurer_rut'] for f in records}
        values = defaultdict(list)
        for f in records:
            values[f['period']].append(f)
        points = {}
        for p in [shift(start, -1)] + all_months:
            rows = values[p]
            complete = latest.get('identity_linked', True) and p in available and all(
                coverage[p, rut].get(family) in ({'validated', 'accepted_with_warning'} if family in {'A', 'F', 'X', 'O'} else {'validated'})
                for rut in cohort for family in families)
            valid = all(not r['unvalued_positions'] and r['value_clp'] is not None for r in rows)
            amount = sum((decimal(r['value_clp']) for r in rows), Decimal(0)) if rows and valid else Decimal(0) if not rows and complete else None
            base = bases.get((kind, run, p))
            share, source, reason = fund_share(rows, base, owners.get((kind, run, p), {}), p, allow_reported)
            points[p] = dict(period=p, value_clp=amount, observed=bool(rows), complete=complete and valid,
                fund_aum=base['parts'] if base else [], fund_aum_complete=base['complete'] if base else False,
                fund_share_pct=share, share_source=source, share_reason=reason,
                insurers=len({r['insurer_rut'] for r in rows}),
                series=sorted({s for r in rows for s in r['series'] or []}),
                delta_clp=None, delta_pct=None, delta_reason=None)
        for p in all_months:
            point, prev = points[p], points.get(shift(p, -1))
            if not prev or not point['complete'] or not prev['complete']:
                point['delta_reason'] = 'missing_or_partial_month'
            elif point['value_clp'] is not None and prev['value_clp'] is not None:
                point['delta_clp'] = point['value_clp']-prev['value_clp']
                if prev['value_clp'] > 0:
                    point['delta_pct'] = point['delta_clp']/prev['value_clp']*100
                else:
                    point['delta_reason'] = 'zero_previous_value'
        name = latest['name']
        if (name or '').strip().upper() in {'NO APLICA', 'N/A', '0', ''}:
            name = key.split(':', 1)[-1]
        output.append(dict(key=key, name=name, kind=kind, run=run,
            tipo=latest['tipo'], grupo=latest['grupo'], category=latest['category'],
            administrators=sorted({r['administrator_name'] or r['administrator_rut'] for r in records if r['administrator_name'] or r['administrator_rut']}),
            currencies=sorted({r['currency'] for r in records if r['currency']}),
            points=[points[p] for p in dates], current=points[end]))
    output.sort(key=lambda r: (r['current']['value_clp'] is None, -(r['current']['value_clp'] or 0), r['key']))
    return dict(dates=dates, rows=output, requested_start=start, requested_end=end,
                requested_months=len(all_months), loaded_months=len(loaded),
                available_start=min(available) if available else None, available_end=max(available) if available else None,
                delta_note='Variación de valor observado entre meses consecutivos con archivos utilizables; no son flujos ni compraventas.')
