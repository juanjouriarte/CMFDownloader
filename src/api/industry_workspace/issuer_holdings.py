"""Issuer-wide holders, with weights kept within a fund and reporting date."""
import json
import re
from collections import defaultdict

from fastapi import HTTPException


def issuer_filter(issuer, issuer_id=None):
    if not issuer_id:
        return 'issuer=:issuer', {'issuer': issuer}
    try:
        identity = json.loads(issuer_id)
    except (ValueError, TypeError):
        identity = None
    if not isinstance(identity, list) or not all(isinstance(x, str) for x in identity):
        raise HTTPException(422, 'Invalid issuer identity')
    if len(identity) == 2 and identity[0] == 'rut' and identity[1].strip():
        rut = re.sub(r'[.\s-]', '', identity[1]).upper()
        return "upper(regexp_replace(issuer_rut, '[.[:space:]-]', '', 'g'))=:rut", {'rut': rut}
    if len(identity) == 3 and identity[0] == 'name' and identity[2].strip():
        return """NULLIF(trim(issuer_rut),'') IS NULL
            AND upper(trim(COALESCE(country_code,'')))=:country
            AND upper(trim(regexp_replace(issuer_name,'[[:space:]]+',' ','g')))=:name""", {
                'country': identity[1].strip().upper(), 'name': ' '.join(identity[2].split()).upper()}
    raise HTTPException(422, 'Unidentified issuer cannot be grouped')


def group_holders(positions):
    from .router import finite
    groups = defaultdict(list)
    for position in positions:
        groups[(position['kind'], position['run'], position['periodo'])].append(position)
    result = []
    for (kind, run, period), items in groups.items():
        weights = [finite(p['weight']) for p in items]
        result.append(dict(kind=kind, run=run, periodo=period,
                           fund_name=items[0]['fund_name'], admin=items[0]['admin'],
                           weight=sum(weights) if all(w is not None for w in weights) else None,
                           invalid_weights=sum(w is None for w in weights),
                           positions=items))
    return sorted(result, key=lambda r: (r['weight'] is None, -(r['weight'] or 0), r['run'], str(r['periodo'])))
