"""Insurer matrices for primary assets; identifiers never inferred from names."""
from datetime import date
from .insurance_history import build_history, shift

FAMILIES = {
    'fixed_income': ['I', 'X'], 'equities': ['A', 'X'],
    'real_estate': ['B', 'X'], 'other': ['O'], 'subsidiaries': ['X'],
    'funds': ['A', 'F', 'X'],
}
# Private identifiers are local to an insurer. No borrower identity is exposed.
LOCAL_TYPES = "'MHA','MHB','CLEAS','CS','DPL','DPC','OIE','BR'"
IDENTITY = f"""CASE
 WHEN r.family='B' AND NULLIF(trim(r.fields->>'property_roll'),'') IS NOT NULL
      AND translate(trim(r.fields->>'property_roll'),'- ','') !~ '^0+$'
 THEN jsonb_build_array('property',r.insurer_rut,r.fields->>'municipality',r.fields->>'city',r.fields->>'property_roll')::text
 WHEN r.family='O' AND COALESCE(NULLIF(trim(r.identifier),''),NULLIF(trim(r.fields->>'asset_code'),'')) IS NOT NULL
 THEN jsonb_build_array('other',r.insurer_rut,r.instrument_type,r.identifier,r.fields->>'asset_code',r.currency)::text
 WHEN NULLIF(trim(r.identifier),'') IS NOT NULL AND trim(r.identifier) !~ '^0+$'
      AND upper(trim(r.identifier)) NOT IN ('NO APLICA','N/A','S/I','SIN INFORMACION','-')
 THEN jsonb_build_array('instrument',r.asset_class,r.instrument_type,trim(r.identifier),
      NULLIF(trim(r.series),''),r.currency,r.issuer_rut,r.country,
      CASE WHEN r.instrument_type IN ({LOCAL_TYPES}) THEN r.insurer_rut END)::text
 ELSE NULL END"""


def positions(where, by_asset=False, by_issuer=False):
    issuer = "CASE WHEN r.issuer_rut ~ '^[0-9]{1,9}$' AND r.issuer_rut !~ '^0+$' THEN r.issuer_rut END"
    identity = 'r.asset_class' if by_asset else (
        f"CASE WHEN ({issuer}) IS NOT NULL THEN jsonb_build_array('issuer',r.asset_class,({issuer}))::text ELSE ({IDENTITY}) END"
        if by_issuer else IDENTITY)
    name = 'r.asset_class' if by_asset else """CASE WHEN r.family='B' THEN
        COALESCE(NULLIF(trim(concat_ws(' · ',NULLIF(r.fields->>'address',''),NULLIF(r.fields->>'city',''))),''),r.display_name)
        ELSE COALESCE(NULLIF(r.issuer_name,''),
          CASE WHEN upper(trim(r.display_name)) NOT IN ('NO APLICA','N/A','0','') THEN r.display_name END,
          NULLIF(r.identifier,''),r.instrument_type) END"""
    return f"""WITH source AS (
      SELECT r.id detail_id,i.period,r.insurer_rut,f.insurer_name,r.asset_class,
        r.instrument_type,r.identifier,r.series,r.currency,r.final_value_clp,
        r.issuer_rut,
        ({identity}) identity,({name}) name,
        (r.data_quality<>'validated' OR r.quality_flags<>'[]'::jsonb) warning
      FROM insurance_records r JOIN insurance_imports i ON i.id=r.import_id
      JOIN insurance_files f ON f.id=r.file_id WHERE {where}
    ), positions AS (
      SELECT *,CASE WHEN identity IS NULL THEN 'unlinked:'||detail_id ELSE 'position:'||md5(identity) END key,
        identity IS NOT NULL identity_linked FROM source
    ), holdings AS (
      SELECT key,period,insurer_rut,max(insurer_name) insurer_name,max(name) name,
        max(asset_class) asset_class,max(instrument_type) instrument_type,max(identifier) identifier,
        max(issuer_rut) issuer_rut,
        array_agg(DISTINCT series) FILTER(WHERE series IS NOT NULL) series,
        array_agg(DISTINCT currency) FILTER(WHERE currency IS NOT NULL) currencies,
        sum(final_value_clp) value_clp,count(*) positions,
        count(*) FILTER(WHERE final_value_clp IS NULL) unvalued_positions,
        count(*) FILTER(WHERE warning) warning_positions,min(detail_id) detail_id,bool_and(identity_linked) identity_linked
      FROM positions GROUP BY key,period,insurer_rut
    )"""


def matrix(read, where, params, labels, asset, limit, offset, group_by="instrument", issuer=None):
    # Only selected columns are returned; insurer totals cover the entire scope.
    grouped = bool(asset) and group_by == "issuer"
    # Issuer drill-down narrows columns, not the insurer's category denominator.
    sql = positions(where, by_asset=not asset, by_issuer=grouped)
    sql += """, columns AS (
      SELECT key,max(name) name,max(asset_class) asset_class,max(instrument_type) instrument_type,
        max(identifier) identifier,max(issuer_rut) issuer_rut,sum(value_clp) value_clp,sum(positions) positions,
        sum(unvalued_positions) unvalued_positions,bool_and(identity_linked) identity_linked
      FROM holdings WHERE (:issuer IS NULL OR issuer_rut=:issuer) GROUP BY key
    ), page AS (SELECT * FROM columns ORDER BY value_clp DESC NULLS LAST,key LIMIT :limit OFFSET :offset),
    totals AS (SELECT insurer_rut,sum(value_clp) value_clp,sum(positions) positions,
        count(*) instruments FROM holdings GROUP BY insurer_rut)
    SELECT jsonb_build_object('columns',COALESCE((SELECT jsonb_agg(p ORDER BY value_clp DESC NULLS LAST,key) FROM page p),'[]'),
      'cells',COALESCE((SELECT jsonb_agg(h) FROM holdings h JOIN page USING(key)),'[]'),
      'totals',COALESCE((SELECT jsonb_agg(t) FROM totals t),'[]'),
      'total_columns',(SELECT count(*) FROM columns),
      'max_value',(SELECT max(abs(value_clp)) FROM holdings),
      'value_clp',(SELECT sum(value_clp) FROM totals)) result"""
    result = read(sql, params | dict(limit=limit, offset=offset, issuer=issuer))[0]['result']
    if not asset:
        for col in result['columns']:
            col['name'] = labels.get(col['asset_class'], col['asset_class'])
    result.update(limit=limit, offset=offset, amount_unit='CLP', group_by='issuer' if grouped else 'instrument')
    return result


def history(read, where, params, labels, asset, insurer, start, end, limit, offset, sort, direction, mode):
    families = FAMILIES.get(asset, sorted({f for fs in FAMILIES.values() for f in fs}))
    reports = read("""SELECT i.period,f.insurer_rut,f.family,f.status
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period BETWEEN :start AND :end
        AND f.insurer_rut=:insurer""", params | dict(insurer=insurer))
    available = [r['period'] for r in read("SELECT period FROM insurance_imports WHERE active AND entity_group='CSVID' ORDER BY period")]
    def complete(period):
        states = {r['family']: r['status'] for r in reports if r['period'] == period}
        return period in available and all(states.get(f) in ({'validated','accepted_with_warning'} if f in {'A','F','X','O'} else {'validated'}) for f in families)
    sort_period = end if sort in {'name','current'} else date.fromisoformat(sort)
    # Sort against the same eligible month-on-month value that the UI displays.
    current = "CASE WHEN n IS NULL AND :current_complete AND linked THEN 0 WHEN missing=0 THEN value END"
    previous = "CASE WHEN prev_n IS NULL AND :previous_complete AND linked THEN 0 WHEN prev_missing=0 THEN prev_value END"
    sort_value = current if mode == 'value' or sort == 'current' else f"""CASE
        WHEN :current_complete AND :previous_complete AND linked THEN
        ({current})-({previous}) END"""
    if mode == 'deltaPct' and sort != 'current':
        sort_value = f"({sort_value}) / NULLIF(CASE WHEN ({previous})>0 THEN ({previous}) END,0)*100"
    order = f"{'name' if sort == 'name' else sort_value} {'ASC' if direction == 'asc' else 'DESC'} NULLS LAST,key"
    sql = positions(where, by_asset=not asset)
    sql += f""", ranked AS (
      SELECT key,max(name) name,bool_and(identity_linked) linked,
        sum(value_clp) FILTER(WHERE period=:sort_period) value,
        sum(positions) FILTER(WHERE period=:sort_period) n,
        sum(unvalued_positions) FILTER(WHERE period=:sort_period) missing,
        sum(value_clp) FILTER(WHERE period=:previous_period) prev_value,
        sum(positions) FILTER(WHERE period=:previous_period) prev_n,
        sum(unvalued_positions) FILTER(WHERE period=:previous_period) prev_missing
      FROM holdings GROUP BY key
    ), page AS (SELECT key,row_number() OVER(ORDER BY {order}) rank FROM ranked
      ORDER BY {order} LIMIT :limit OFFSET :offset)
    SELECT jsonb_build_object('total',(SELECT count(*) FROM ranked),
      'holdings',COALESCE((SELECT jsonb_agg(h) FROM holdings h JOIN page USING(key)),'[]'),
      'keys',COALESCE((SELECT jsonb_agg(key ORDER BY rank) FROM page),'[]')) result"""
    result = read(sql, params | dict(limit=limit, offset=offset, sort_period=sort_period,
        previous_period=shift(sort_period,-1), current_complete=complete(sort_period), previous_complete=complete(shift(sort_period,-1))))[0]['result']
    raw = result['holdings']
    adapted = []
    for r in raw:
        adapted.append(r | dict(period=date.fromisoformat(r['period']), fund_key=r['key'],
            fund_kind=r['instrument_type'] if asset else None, fund_run=r['identifier'] if asset else None,
            tipo=labels.get(r['asset_class'],r['asset_class']), grupo=None, category=None,
            name=r['name'] if asset else labels.get(r['asset_class'],r['asset_class']),
            administrator_rut=None, administrator_name=None, currency=None, coverage_families=families))
    data = build_history(adapted, reports, available, start, end, {}, {}, allow_reported=False)
    rank = {k:i for i,k in enumerate(result['keys'])}
    for row in data['rows']:
        matches = [r for r in raw if r['key']==row['key']]
        last = max(matches, key=lambda r:r['period'])
        row.update(detail_id=last['detail_id'] if asset else None,
                   identity_linked=last['identity_linked'],
                   currencies=sorted({c for r in matches for c in r['currencies'] or []}))
    data['rows'].sort(key=lambda r:rank[r['key']])
    return data | dict(total=result['total'],limit=limit,offset=offset)
