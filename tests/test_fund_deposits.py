from copy import deepcopy
from datetime import date, datetime, timezone
from html import escape
import os
import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from src.etl.fundDeposits.parser import HEADERS, parse_html, document
from src.etl.fundDeposits.downloader import load_snapshot
from src.api import fund_deposits as api


def row(registration='FM260001',run='10939',kind='Fondo Inversión No Rescatable',admin='BTG AGF'):
    cells=[registration,'13/10/2026',run,'FONDO DE INVERSIÓN PRUEBA',admin,'19/10/2026','Vigente','',
        '<a href="javascript:void(0)" onclick="verArchivo(\'http://www.svs.cl/documentos/rfm/rfm_2026100657271.pdf\')">2026100657271</a>',
        '2026100666487','', '', '', '<a onclick="verArchivo(\'http://www.svs.cl/documentos/rfm/cgm_.pdf\')"></a>',
        '', '', '',kind,'']
    return '<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>'


def html(*rows,mirror=True):
    table='<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in HEADERS)+'</tr>'+''.join(rows)+'</table>'
    return '<html><div id="tabla_resultado">'+table+'</div>'+('<div id="tabla_resultado">'+table+'</div>' if mirror else '')+'</html>'


def test_parser_mirrors_types_reused_registration_and_documents():
    records=parse_html(html(row(),row(run='10938',kind='Fondo Mutuo'),row('FM260002','10937','Fondo Inversión Rescatable')))
    assert len(records)==3
    assert {r['fund_type'] for r in records}=={'FM','FIR','FNR'}
    assert records[0]['deposit_date']==date(2026,10,13)
    assert records[0]['modified_date']==date(2026,10,19)
    assert records[0]['documents'][0]['url']=='https://www.cmfchile.cl/documentos/rfm/rfm_2026100657271.pdf'
    assert records[0]['documents'][1]['url'] is None
    assert len(records[0]['documents'])==2  # Empty CMF placeholder is not a document.


@pytest.mark.parametrize('bad',[
    '<html>maintenance</html>',html(row()).replace('</html>',''),html(row()).replace('RUN del Fondo','New column'),
    html(row()).replace('13/10/2026','99/10/2026'),html(row()).replace('Fondo Inversión No Rescatable','Unknown type'),
    html(row(),row()),html(),html(row()).replace('www.svs.cl','evil.example'),
    html(row()).replace('</table>','',1),
])
def test_parser_rejects_incomplete_changed_or_unsafe_sources(bad):
    with pytest.raises(ValueError):parse_html(bad)


def test_mirrored_tables_must_agree():
    value=html(row()).replace('BTG AGF','OTHER AGF',1)
    with pytest.raises(ValueError,match='disagree'):parse_html(value)


def test_blank_source_name_is_preserved_not_invented():
    record=parse_html(html(row().replace('FONDO DE INVERSIÓN PRUEBA','')))[0]
    assert record['fund_name']=='' and record['run']=='10939'


def test_relative_approval_link():
    cell=BeautifulSoup('<td><a onclick="ventana_url(\'/documentos/res/res_2007_528.pdf\')">528 (año 2007)</a></td>','html.parser').td
    assert document(cell,'approval','Resolución')['url']=='https://www.cmfchile.cl/documentos/res/res_2007_528.pdf'


def test_api_rejects_invalid_filters():
    app=FastAPI();app.include_router(api.router);client=TestClient(app)
    for query in ['fund_type=FI','limit=201','offset=-1','date_field=run','status=bad','date_from=2026-10-20&date_to=2026-10-01']:
        assert client.get('/industry-workspace/new-funds?'+query).status_code==422


@pytest.fixture
def db(monkeypatch):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':pytest.skip('Enable local transactional fixtures')
    from src.db.engine import engine
    from sqlalchemy.orm import Session
    assert engine.url.host in ('localhost','127.0.0.1','::1')
    connection=engine.connect();transaction=connection.begin();session=Session(bind=connection)
    for table in ('fund_deposits','fund_deposit_imports','fund_deposit_revisions'):
        session.execute(text(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP'))
    monkeypatch.setattr(api,'_read',lambda sql,params=None:[dict(r) for r in session.execute(text(sql),params or {}).mappings()])
    try:yield session
    finally:session.close();transaction.rollback();connection.close()


def load(db,records):
    return load_snapshot(records,fetched_at=datetime.now(timezone.utc),source_sha256='test',session=db)


def test_snapshot_idempotency_changes_and_missing_rows(db):
    records=parse_html(html(*(row('FM26000'+str(i),str(10930+i)) for i in range(5))))
    assert load(db,records).rows_upserted==5
    assert load(db,records).rows_upserted==0
    assert db.scalar(text('SELECT count(*) FROM fund_deposit_revisions'))==5
    changed=deepcopy(records);changed[0]['documents'][0]['url']='https://www.cmfchile.cl/documentos/rfm/rfm_new.pdf'
    changed[0]['administrator']='New AGF'
    assert load(db,changed).rows_upserted==1
    assert db.scalar(text('SELECT count(*) FROM fund_deposit_revisions'))==6
    assert load(db,changed[:4]).rows_upserted==0
    assert db.scalar(text('SELECT count(*) FROM fund_deposits WHERE NOT source_present'))==1
    assert db.scalar(text('SELECT count(*) FROM fund_deposits WHERE liquidated'))==0
    with pytest.raises(ValueError,match='20%'):load(db,changed[:1])
    assert db.scalar(text('SELECT count(*) FROM fund_deposits'))==5
    assert db.scalar(text('SELECT count(*) FROM fund_deposit_imports'))==4
    assert load(db,changed).rows_upserted==1  # Reappearing row preserved.


def test_api_combined_filters_dates_pagination_and_registry_without_nav(db):
    records=parse_html(html(row(),row('FM260002','10938','Fondo Mutuo','OTHER AGF'),row('FM260003','10937','Fondo Inversión Rescatable')))
    records[1]['active']=False;records[1]['liquidated']=True;records[1]['deposit_date']=date(2011,7,1)
    load(db,records)
    app=FastAPI();app.include_router(api.router);client=TestClient(app);base='/industry-workspace/new-funds'
    all_rows=client.get(base).json();assert all_rows['total']==3
    assert all_rows['fm']==all_rows['fir']==all_rows['fnr']==1
    result=client.get(base,params={'administrator':'BTG AGF','fund_type':'FNR','status':'active','date_from':'2026-10-01','date_to':'2026-10-19'}).json()
    assert result['total']==1 and result['rows'][0]['run']=='10939'
    assert client.get(base+'?date_from=2026-10-01').json()['total']==2
    assert client.get(base+'?date_field=modified&date_from=2026-10-01').json()['total']==3
    assert client.get(base+'?status=liquidated').json()['total']==1
    assert client.get(base+'?search=10939').json()['total']==1
    assert client.get(base+'?search=%25').json()['total']==0
    assert client.get(base+'?search=%27%20OR%201%3D1--').json()['total']==0
    pages=[client.get(base+f'?limit=1&offset={i}').json()['rows'][0]['run'] for i in range(3)]
    assert len(set(pages))==3
    assert client.get(base+'?offset=99').json()['total']==3
    assert client.get(base+'/directory').json()['last_import']['row_count']==3


def test_daily_job_registered():
    from apscheduler.schedulers.background import BackgroundScheduler
    from src.scheduler import register_jobs
    scheduler=BackgroundScheduler(timezone='America/Santiago');register_jobs(scheduler)
    job=scheduler.get_job('fund_deposits')
    assert job is not None and job.max_instances==1 and job.coalesce
    assert "hour='8'" in str(job.trigger) and "minute='35'" in str(job.trigger)
