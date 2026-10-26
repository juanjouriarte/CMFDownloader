"""Read-only CMF registry, independent of NAV availability and currency."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text
from src.db.engine import SessionLocal
from src.etl.fundDeposits.parser import SOURCE_URL, FUND_TYPES

router=APIRouter(prefix='/industry-workspace/new-funds',tags=['fund-deposits'])


def _read(sql,params=None):
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        session.execute(text("SET LOCAL statement_timeout='10s'"))
        return [dict(r) for r in session.execute(text(sql),params or {}).mappings()]


@router.get('/directory')
def directory(response:Response):
    response.headers['Cache-Control']='no-cache'
    admins=_read('''SELECT administrator name,count(*) funds FROM fund_deposits
        WHERE source_present GROUP BY administrator ORDER BY administrator''')
    dates=_read('''SELECT min(deposit_date) first_date,max(deposit_date) last_deposit,
        max(modified_date) last_modification FROM fund_deposits WHERE source_present''')[0]
    last=_read('SELECT fetched_at,row_count,inserted,updated FROM fund_deposit_imports ORDER BY id DESC LIMIT 1')
    return dict(administrators=admins,fund_types=FUND_TYPES,dates=dates,
                last_import=last[0] if last else None,source_url=SOURCE_URL,
                schedule='08:35 America/Santiago · diario')


@router.get('')
def deposits(response:Response,
    administrator:list[str]|None=Query(None,max_length=50),
    fund_type:Literal['FM','FIR','FNR']|None=None,
    status:Literal['all','active','inactive','liquidated']='all',
    date_field:Literal['deposit','modified']='deposit',
    date_from:date|None=None,date_to:date|None=None,
    search:str=Query('',max_length=150),
    limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),
):
    if date_from and date_to and date_from>date_to:
        raise HTTPException(422,'La fecha inicial debe ser anterior a la final.')
    response.headers['Cache-Control']='no-cache'
    clauses=['source_present'];params=dict(limit=limit,offset=offset)
    column='deposit_date' if date_field=='deposit' else 'modified_date'
    if administrator:
        if any(not a.strip() or len(a)>300 for a in administrator):
            raise HTTPException(422,'Administradora inválida.')
        clauses.append('administrator=ANY(:admins)');params['admins']=administrator
    if fund_type:clauses.append('fund_type=:fund_type');params['fund_type']=fund_type
    if status=='active':clauses.append('active')
    elif status=='inactive':clauses.append('NOT active')
    elif status=='liquidated':clauses.append('liquidated')
    for value,operator,key in [(date_from,'>=','start'),(date_to,'<=','end')]:
        if value:clauses.append(f'{column}{operator}:{key}');params[key]=value
    if search.strip():
        clauses.append("concat_ws(' ',fund_name,run,registration,administrator) ILIKE :search")
        params['search']='%'+search.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    where=' AND '.join(clauses)
    return _read(f'''WITH filtered AS (SELECT * FROM fund_deposits WHERE {where}),
      page AS (SELECT * FROM filtered ORDER BY {column} DESC,registration DESC,run LIMIT :limit OFFSET :offset)
      SELECT count(*) total,count(DISTINCT administrator) administrators,
        count(*) FILTER(WHERE fund_type='FM') fm,count(*) FILTER(WHERE fund_type='FIR') fir,
        count(*) FILTER(WHERE fund_type='FNR') fnr,max(deposit_date) latest_deposit,
        COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.{column} DESC,p.registration DESC,p.run) FROM page p),'[]'::jsonb) rows
      FROM filtered''',params)[0]
