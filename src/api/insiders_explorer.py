"""Insiders workspace: filterable disclosures and exact-RUT ownership links."""
import json
from datetime import date
from functools import lru_cache
from time import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from .insiders import _read, _serialize, _with_broker_names
from src.etl.insiders.codes import CODES

router = APIRouter(prefix='/insiders', tags=['insiders'])
OWNER_RUT = "CASE WHEN trim(a.rut) ~ '^[0-9.]+(-[0-9kK])?$' THEN NULLIF(LTRIM(regexp_replace(split_part(trim(a.rut),'-',1),'[^0-9]','','g'),'0'),'') END"


def _ruts(values):
    if len(values)>50 or any(not v.isdigit() or len(v)>20 for v in values):
        raise HTTPException(422,'Select up to 50 numeric RUTs per filter')
    return list(dict.fromkeys(v.lstrip('0') or '0' for v in values))


@lru_cache(maxsize=2)
def owner_index(bucket):
    return {r['rut']:r for r in _read(f'''SELECT {OWNER_RUT} rut,max(periodo) last_period,
        count(DISTINCT run_fondo) historical_funds FROM aportantes_fi a
        WHERE {OWNER_RUT} IS NOT NULL GROUP BY 1''')}


@lru_cache(maxsize=2)
def directory_data(bucket):
    companies=_read('''SELECT company_rut rut,(array_agg(company_name ORDER BY transaction_date DESC,id DESC))[1] name,
        count(*) disclosures,max(transaction_date) latest FROM insider_transactions WHERE is_current GROUP BY company_rut ORDER BY name''')
    informants=_read('''SELECT informant_rut rut,
        (array_agg(concat_ws(' ',informant_name,paternal_name,maternal_name) ORDER BY transaction_date DESC,id DESC))[1] name,
        count(*) disclosures,max(transaction_date) latest FROM insider_transactions
        WHERE is_current AND informant_rut ~ '^[0-9]+$' AND informant_rut<>'0' GROUP BY informant_rut ORDER BY name''')
    owners=owner_index(bucket)
    informants=[dict(r,owner_period=owners.get(r['rut'].lstrip('0'),{}).get('last_period')) for r in informants]
    return {'companies':companies,'informants':informants,'codes':CODES}


@router.get('/directory')
def directory(response: Response):
    response.headers['Cache-Control']='public, max-age=60'
    return directory_data(int(time()//60))


@router.get('/explorer')
def explorer(response: Response,
    company_ruts: list[str] = Query(default=[]), informant_ruts: list[str] = Query(default=[]),
    date_from: date | None = None, date_to: date | None = None,
    date_field: Literal['transaction','communication'] = 'transaction',
    transaction_type: str = Query('',max_length=20), relationship_code: str = Query('',max_length=20),
    security_type: str = Query('',max_length=20), entity_status: Literal['','VI','NV'] = '',
    owner_only: bool = False, amended_only: bool = False,
    search: str = Query('',max_length=120),
    sort: Literal['date','amount','units'] = 'date', direction: Literal['asc','desc'] = 'desc',
    limit: int = Query(30,ge=1,le=500), offset: int = Query(0,ge=0)):
    if date_from and date_to and date_from>date_to:
        raise HTTPException(422,'Invalid date range')
    companies,informants=_ruts(company_ruts),_ruts(informant_ruts)
    owners=owner_index(int(time()//60))
    clauses=['is_current'];params={'limit':limit,'offset':offset}
    column='transaction_date' if date_field=='transaction' else 'communication_date'
    for key,value in [('company_rut',companies),('informant_rut',informants)]:
        if value:clauses.append(f'{key}=ANY(:{key})');params[key]=value
    for key,value in [('transaction_type',transaction_type),('relationship_code',relationship_code),('security_type',security_type)]:
        if value:clauses.append(f'{key}=:{key}');params[key]=value.upper()
    if entity_status:
        clauses.append("(source_entities @> CAST(:source_status AS jsonb) OR (source_entities='[]'::jsonb AND entity_status=:entity_status))")
        params.update(source_status=json.dumps([{'status':entity_status}]),entity_status=entity_status)
    if date_from:clauses.append(f'{column}>=:date_from');params['date_from']=date_from
    if date_to:clauses.append(f'{column}<=:date_to');params['date_to']=date_to
    if owner_only:clauses.append("LTRIM(informant_rut,'0')=ANY(:owners)");params['owners']=list(owners)
    if amended_only:clauses.append('is_amendment')
    if search.strip():
        clauses.append("concat_ws(' ',company_name,informant_name,paternal_name,maternal_name,ticker,company_rut,informant_rut) ILIKE :search")
        params['search']='%'+search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    where=' AND '.join(clauses)
    order={'date':column,'amount':'amount_clp','units':'units'}[sort]
    exact=','.join(f"'{k}',p.{k}::text" for k in ['units','unit_price_or_rate','amount_clp','operation_pct','final_holding_pct'])
    data=_read(f'''WITH filtered AS MATERIALIZED (SELECT * FROM insider_transactions WHERE {where}),
      page AS (SELECT * FROM filtered ORDER BY {order} {direction} NULLS LAST,transaction_date DESC,id DESC LIMIT :limit OFFSET :offset),
      monthly AS (SELECT date_trunc('month',{column})::date AS month,count(*) reports,
        count(*) FILTER(WHERE transaction_type='A') acquisitions,
        count(*) FILTER(WHERE transaction_type='E') disposals,
        count(*) FILTER(WHERE transaction_type NOT IN ('A','E')) other FROM filtered WHERE {column} IS NOT NULL GROUP BY 1),
      leaders AS (SELECT company_rut rut,(array_agg(company_name ORDER BY transaction_date DESC,id DESC))[1] name,count(*) reports
        FROM filtered GROUP BY company_rut ORDER BY reports DESC,name LIMIT 5)
      SELECT (SELECT count(*) FROM filtered) total,
        (SELECT jsonb_build_object('companies',count(DISTINCT company_rut),'informants',count(DISTINCT informant_rut),
          'acquisitions',count(*) FILTER(WHERE transaction_type='A'),'disposals',count(*) FILTER(WHERE transaction_type='E'),
          'amendments',count(*) FILTER(WHERE is_amendment),'latest',max({column})) FROM filtered) summary,
        coalesce((SELECT jsonb_agg((to_jsonb(p)-'raw_cells'-'fingerprint')||jsonb_build_object({exact})
          ORDER BY p.{order} {direction} NULLS LAST,p.transaction_date DESC,p.id DESC) FROM page p),'[]'::jsonb) rows,
        coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY month) FROM monthly m),'[]'::jsonb) monthly,
        coalesce((SELECT jsonb_agg(to_jsonb(l) ORDER BY reports DESC,name) FROM leaders l),'[]'::jsonb) leaders
    ''',params)[0]
    for row in _with_broker_names(data['rows'], _read):
        row.update(_serialize(row))
        row['owner_period']=owners.get((row.get('informant_rut') or '').lstrip('0'),{}).get('last_period')
    response.headers['Cache-Control']='public, max-age=60'
    return {**data,'limit':limit,'offset':offset,'date_field':date_field}


@router.get('/connections')
def connections(response: Response, rut: str = Query(...,pattern=r'^[0-9]{1,20}$'), as_of: date | None = None):
    rut=rut.lstrip('0')
    if not rut:raise HTTPException(422,'An identified RUT is required')
    cutoff=as_of or date.today()
    # Latest source quarter for the universe, never a future report relative to the filter.
    period=_read('SELECT max(periodo) period FROM aportantes_fi WHERE periodo<=:cutoff',{'cutoff':cutoff})[0]['period']
    info=_read(f'''SELECT max(a.periodo) last_seen FROM aportantes_fi a
        WHERE {OWNER_RUT}=:rut AND a.periodo<=:cutoff''',{'rut':rut,'cutoff':cutoff})[0]
    funds=_read(f'''SELECT a.run_fondo run,coalesce(f.razon_social,a.run_fondo) name,f.administrador admin,
        CASE WHEN bool_and(a.pct_propiedad BETWEEN 0 AND 100) AND count(a.pct_propiedad)=count(*)
             AND sum(a.pct_propiedad)<=100 THEN sum(a.pct_propiedad)::text END weight,
        f.moneda currency,coalesce(f.vigente,false) AND EXISTS(SELECT 1 FROM valores_cuota_fi v WHERE v.run_fondo=a.run_fondo) can_open
        FROM aportantes_fi a LEFT JOIN fondos_inversion f USING(run_fondo)
        WHERE {OWNER_RUT}=:rut AND a.periodo=:period
        GROUP BY a.run_fondo,f.razon_social,f.administrador,f.moneda,f.vigente
        ORDER BY sum(a.pct_propiedad) DESC NULLS LAST,name''',{'rut':rut,'period':period}) if period else []
    response.headers['Cache-Control']='public, max-age=60'
    return {'rut':rut,'as_of':cutoff,'period':period,'last_seen':info['last_seen'],'funds':funds,
            'match_method':'exact_rut','basis':'Reported FI shareholders, not a complete register or proof of beneficial ownership.'}
