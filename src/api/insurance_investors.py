"""Fund RUN groups with insurer-level monthly positions, without NAV estimation."""
from collections import defaultdict
from .insurance_history import build_history, decimal


def add_investors(data, holdings, reports, available, start, end):
    # Reuse the same coverage/zero rules as the existing monthly history. A
    # composite key separates insurer cohorts before calculating any changes.
    identities, adapted = {}, []
    for row in holdings:
        identity = (row['fund_key'], row['insurer_rut'])
        key = str(identity)
        identities[key] = identity
        adapted.append(row | {'fund_key': key})
    history = build_history(adapted, reports, available, start, end, {}, {}, allow_reported=False)
    names = {r['insurer_rut']: r['insurer_name'] for r in holdings}
    scope = defaultdict(list)
    for row in holdings:
        scope[row['insurer_rut'], row['period']].append(row)
    totals = {}
    for key, rows in scope.items():
        totals[key] = sum((decimal(r['value_clp']) for r in rows), decimal(0)) if all(
            r['value_clp'] is not None and not r['unvalued_positions'] for r in rows) else None
    investors = defaultdict(list)
    for row in history['rows']:
        fund_key, rut = identities[row['key']]
        for point in row['points'] + [row['current']]:
            total = totals.get((rut, point['period']))
            point['insurer_scope_value_clp'] = total
            point['insurer_scope_pct'] = point['value_clp'] / total * 100 if (
                point['value_clp'] is not None and total is not None and total > 0) else None
        investors[fund_key].append(dict(rut=rut, name=names.get(rut) or rut,
            points=row['points'], current=row['current']))
    for row in data['rows']:
        row['investors'] = sorted(investors[row['key']], key=lambda r: (
            r['current']['value_clp'] is None, -(r['current']['value_clp'] or 0), r['rut']))
    return data
