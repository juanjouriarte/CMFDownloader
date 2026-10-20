"""Read-only CMF Article 12 disclosures. Rows are reports, not unique trades."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from src.db.engine import SessionLocal
from src.etl.insiders.codes import CODES, SOURCE_URL
from src.etl.insiders.downloader import HISTORY_START, PAGE_URL, month_windows, today_chile

router = APIRouter(prefix='/insiders', tags=['insiders'])


def _read(sql, params=None):
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        return [dict(row) for row in session.execute(text(sql),params or {}).mappings()]


def _broker_rut(value):
    """Normalize an identified RUT body, never guess from arbitrary text."""
    value = (value or '').strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:-[0-9kK])?', value):
        return None
    return value.split('-')[0].replace('.', '').lstrip('0') or None


def _with_broker_names(rows, read):
    # One indexed lookup for the page, not a query for each disclosure.
    ruts = sorted({rut for row in rows if (rut := _broker_rut(row.get('broker_rut')))})
    names = {r['rut']:r['razon_social'] for r in read(
        "SELECT rut, NULLIF(trim(razon_social),'') razon_social FROM emisores WHERE rut=ANY(:ruts)",
        {'ruts':ruts},
    )} if ruts else {}
    for row in rows:
        row['broker_name'] = names.get(_broker_rut(row.get('broker_rut')))
        row['broker_name_source'] = 'SII' if row['broker_name'] else None
    return rows


def _serialize(row):
    result={k:str(v) if isinstance(v,Decimal) else v for k,v in row.items()}
    for key,domain in [('relationship_code','relationship'),('transaction_type','transaction_type'),('security_type','security_type'),('entity_status','entity_status')]:
        result[key+'_label']=CODES[domain].get(row[key],row[key])
    result['informant_display_name']=' '.join(str(row[k]) for k in ('informant_name','paternal_name','maternal_name') if row.get(k))
    result['currency']='CLP'
    ref=row.get('amendment_reference')
    result['amendment_url']=(f"https://www.cmfchile.cl/institucional/inc/tra/ver_bitacora.php?seq={ref}&rut={row['company_rut']}&tipo=12v" if ref else None)
    return result


@router.get('/codes')
def codes(response: Response):
    response.headers['Cache-Control']='public, max-age=3600'
    return {'source_url':SOURCE_URL,'codes':CODES}


@router.get('/transactions')
def transactions(
    response: Response,
    date_from: date | None = None,
    date_to: date | None = None,
    date_field: Literal['transaction','communication'] = 'transaction',
    entity_status: Literal['VI','NV'] | None = None,
    company_rut: str | None = Query(None,max_length=20,pattern=r'^\d+$'),
    informant_rut: str | None = Query(None,max_length=20,pattern=r'^\d+$'),
    ticker: str | None = Query(None,max_length=100),
    relationship_code: str | None = Query(None,max_length=20),
    transaction_type: str | None = Query(None,max_length=20),
    security_type: str | None = Query(None,max_length=20),
    amended_only: bool = False,
    search: str | None = Query(None,max_length=120),
    limit: int = Query(50,ge=1,le=500),
    offset: int = Query(0,ge=0),
):
    if date_from and date_to and date_from>date_to:
        raise HTTPException(422,'date_from must be on or before date_to')
    response.headers['Cache-Control']='public, max-age=60'
    clauses=['is_current'];params={'limit':limit,'offset':offset}
    date_column='transaction_date' if date_field=='transaction' else 'communication_date'
    for value,operator,name in [(date_from,'>=','date_from'),(date_to,'<=','date_to')]:
        if value:
            clauses.append(f'{date_column} {operator} :{name}');params[name]=value
    for key,value in [('entity_status',entity_status),('company_rut',company_rut),('informant_rut',informant_rut),('ticker',ticker),('relationship_code',relationship_code),('transaction_type',transaction_type),('security_type',security_type)]:
        if value:
            clauses.append(f'{key}=:{key}');params[key]=value.strip().upper()
    if amended_only:clauses.append('is_amendment')
    if search and search.strip():
        clauses.append("concat_ws(' ',company_name,informant_name,paternal_name,maternal_name,ticker,company_rut,informant_rut) ILIKE :search")
        params['search']='%'+search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    where=' AND '.join(clauses)
    # One statement snapshot; NUMERIC fields travel as strings, preserving decimals.
    numeric=['units','unit_price_or_rate','amount_clp','operation_pct','final_holding_pct']
    exact=','.join(f"'{key}',p.{key}::text" for key in numeric)
    data=_read(f"""WITH filtered AS (SELECT * FROM insider_transactions WHERE {where}),
        page AS (SELECT * FROM filtered ORDER BY {date_column} DESC NULLS LAST,transaction_date DESC,id DESC LIMIT :limit OFFSET :offset)
        SELECT (SELECT count(*) FROM filtered) total,
               coalesce((SELECT jsonb_agg((to_jsonb(p) - 'raw_cells' - 'fingerprint') || jsonb_build_object({exact})
                  ORDER BY p.{date_column} DESC NULLS LAST,p.transaction_date DESC,p.id DESC) FROM page p),'[]'::jsonb) rows""",params)[0]
    rows=_with_broker_names(data['rows'], _read)
    return {'total':data['total'],'limit':limit,'offset':offset,
            'rows':[_serialize({k:v for k,v in r.items() if k not in ('raw_cells','fingerprint')}) for r in rows],
            'source_url':PAGE_URL,'unit':'disclosures',
            'methodology':'CMF Article 12 disclosures; several informants or roles can report the same economic transaction. Amounts are reported pesos; unit_price_or_rate can be a rate. Entity status is observed at import, not historical trading status.'}


@router.get('/coverage')
def coverage(response: Response):
    response.headers['Cache-Control']='no-store'
    summary=_read('''SELECT count(*) FILTER(WHERE is_current) disclosures,
        count(*) FILTER(WHERE NOT is_current) superseded_rows,
        min(transaction_date) FILTER(WHERE is_current) first_transaction,
        max(transaction_date) FILTER(WHERE is_current) last_transaction,
        max(communication_date) FILTER(WHERE is_current) last_communication,
        count(DISTINCT company_rut) FILTER(WHERE is_current) companies,
        count(*) FILTER(WHERE is_current AND entity_status='VI') vigente_disclosures,
        count(*) FILTER(WHERE is_current AND entity_status='NV') no_vigente_disclosures,
        count(*) FILTER(WHERE is_current AND is_amendment) amended_disclosures
        FROM insider_transactions''')[0]
    windows=_read('''SELECT DISTINCT ON(date_from,date_to) date_from,date_to,fetched_at,row_count,sources
        FROM insider_imports ORDER BY date_from,date_to,fetched_at DESC,id DESC''')
    imported={(r['date_from'],r['date_to']) for r in windows}
    missing=[{'date_from':a,'date_to':b} for a,b in month_windows(HISTORY_START,today_chile()) if (a,b) not in imported]
    return {'summary':summary,'windows':windows,'missing_windows':missing,
            'history_start':HISTORY_START,'source_url':PAGE_URL,
            'schedule':'Daily 08:45 America/Santiago; recent 100 days rounded to month start; full history on day 1.',
            'coverage_basis':'Successful VI + NV source snapshots by transaction date; does not establish that every required disclosure was filed.'}
