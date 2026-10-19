from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import os
from unittest.mock import Mock

import pytest
from src.etl.insiders import loader
from src.etl.insiders.downloader import month_windows, InsidersDownloader

START, END = date(2026,9,1), date(2026,9,30)
CELLS = ['12345678','DI','11111111','1','','','SOCIEDAD DE PRUEBA',
         '30/09/2026','01/10/2026','A','','ACC','','S','TEST','','','','UNICA',
         '10000000000000.25','.32','3200000000000','S','22222222','N','.01','0','','NO']


def page(cells=None, count=1):
    cells = cells or CELLS
    headings = [str(i) for i in range(28)]
    for i,h in {0:'RUT Sociedad',7:'Fecha Transaccion',19:'Número de unidades',21:'Monto en pesos',27:'Observaciones'}.items():
        headings[i]=h
    row='<tr>'+''.join(f'<td>{v}</td>' for v in cells)+'</tr>'
    return ('<div id="cuadro"><table><tr><td colspan="29">COMPAÑÍA DE PRUEBA</td></tr>'
            '<tr><th>Modifica Información Enviada</th></tr><tr>'
            + ''.join(f'<th>{h}</th>' for h in headings)+'</tr>'+row*count
            + '</table></div><div id="mod_bitacora"></div>')


def records(cells=None,count=1,status='VI'):
    return loader.parse_html(page(cells,count),status,START,END)


def sources(rows, fetched_at=None):
    return [dict(status=status,row_count=sum(r['entity_status']==status for r in rows),
                 fetched_at=(fetched_at or datetime.now(timezone.utc)).isoformat(),url='https://www.cmfchile.cl/example',sha256='abc') for status in ('VI','NV')]


def test_parser_preserves_decimals_dates_zero_and_source_multiplicity():
    result=records(count=2)
    assert result[0]['units']==Decimal('10000000000000.25')
    assert result[0]['unit_price_or_rate']==Decimal('.32')
    assert result[0]['final_holding_pct']==0
    assert result[0]['communication_date']==date(2026,10,1)
    assert result[0]['instrument_issuer_rut'] is None
    assert result[0]['fingerprint']==result[1]['fingerprint']
    assert [r['occurrence'] for r in result]==[1,2]
    assert records(status='NV')[0]['fingerprint']==result[0]['fingerprint']


def test_timestamp_dates_preserve_disclosure_identity_and_raw_source():
    cells = CELLS.copy()
    cells[7] += ' 00:00:00'
    cells[8] += ' 14:32:09'
    row = records(cells)[0]
    original = records()[0]
    assert row['transaction_date'] == original['transaction_date']
    assert row['communication_date'] == original['communication_date']
    assert row['fingerprint'] == original['fingerprint']
    assert row['raw_cells'][7:9] == cells[7:9]


@pytest.mark.parametrize('value', ['31/09/2026 00:00:00', '30/09/2026 junk',
                                  '30/09/2026 25:00:00', '01/10/2026 00:00:00'])
def test_timestamp_dates_still_reject_invalid_or_out_of_window_values(value):
    cells = CELLS.copy()
    cells[7] = value
    with pytest.raises(loader.SourceFormatError):
        records(cells)


def test_correction_reference_and_unknown_codes_are_preserved():
    cells=CELLS.copy();cells[1]='NEW';cells[28]='<a onclick="bitacora(202609123,12345678)">SI</a>'
    row=records(cells)[0]
    assert row['relationship_code']=='NEW'
    assert row['is_amendment'] and row['amendment_reference']=='202609123'


@pytest.mark.parametrize('html', ['<html>Forbidden</html>',page().replace('id="mod_bitacora"','id="wrong"'),page().replace('RUT Sociedad','Changed heading'),page().replace('<td>UNICA</td>','')])
def test_parser_rejects_error_truncated_or_changed_pages(html):
    with pytest.raises(loader.SourceFormatError):loader.parse_html(html,'VI',START,END)


def test_only_explicit_empty_response_is_accepted():
    assert loader.parse_html('<div id="cuadro"><table><tr><td>SIN INFORMACION</td></tr></table></div><div id="mod_bitacora"></div>','VI',START,END)==[]
    with pytest.raises(loader.SourceFormatError):loader.parse_html('<div id="cuadro"></div><div id="mod_bitacora"></div>','VI',START,END)


@pytest.mark.parametrize('column,value',[(7,'01/10/2026'),(19,'1,234'),(22,'UNKNOWN')])
def test_parser_rejects_out_of_range_or_ambiguous_values(column,value):
    cells=CELLS.copy();cells[column]=value
    with pytest.raises(loader.SourceFormatError):records(cells)


def test_windows_cover_leap_month_and_boundaries():
    assert list(month_windows(date(2024,1,31),date(2024,3,1)))==[(date(2024,1,31),date(2024,1,31)),(date(2024,2,1),date(2024,2,29)),(date(2024,3,1),date(2024,3,1))]


def test_failed_nv_download_never_replaces_existing_data(monkeypatch):
    import src.etl.insiders.downloader as module
    d=InsidersDownloader();monkeypatch.setattr(d,'fetch_window',Mock(side_effect=RuntimeError('NV failed')))
    load=Mock();monkeypatch.setattr(module,'load_window',load)
    with pytest.raises(RuntimeError):d.backfill(START,END)
    load.assert_not_called()


def test_incomplete_status_pair_rejected_before_database(monkeypatch):
    monkeypatch.setattr(loader,'SessionLocal',lambda:pytest.fail('must not open DB'))
    with pytest.raises(ValueError):loader.load_window(START,END,records(),sources(records())[:1])


@pytest.fixture
def local_db(monkeypatch):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':pytest.skip('Opt-in local PostgreSQL rollback fixture')
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from src.db.engine import engine
    assert engine.url.host in ('localhost','127.0.0.1')
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            for table in ['insider_imports','insider_transactions','insider_codes']:
                connection.execute(text(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP'))
                if table!='insider_codes':
                    connection.execute(text(f'ALTER TABLE pg_temp.{table} ALTER COLUMN id DROP DEFAULT'))
                    connection.execute(text(f'ALTER TABLE pg_temp.{table} ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY'))
            monkeypatch.setattr(loader,'SessionLocal',lambda:Session(bind=connection,join_transaction_mode='create_savepoint'))
            yield connection
        finally:
            transaction.rollback()


def test_repeat_import_corrections_status_moves_and_empty_snapshots(local_db):
    from sqlalchemy import text
    conn=local_db
    rows=records(count=2)
    for _ in range(2):loader.load_window(START,END,rows,sources(rows))
    assert conn.execute(text('SELECT count(*) FROM insider_transactions')).scalar()==2
    rows=records(count=2,status='NV');loader.load_window(START,END,rows,sources(rows))
    assert conn.execute(text("SELECT count(*) FROM insider_transactions WHERE entity_status='NV' AND is_current")).scalar()==2
    cells=CELLS.copy();cells[19]='20';cells[28]='SI'
    rows=records(cells);loader.load_window(START,END,rows,sources(rows))
    assert conn.execute(text('SELECT count(*) FROM insider_transactions WHERE is_current')).scalar()==1
    assert conn.execute(text('SELECT count(*) FROM insider_transactions WHERE NOT is_current')).scalar()==2
    loader.load_window(START,END,[],sources([]))
    assert conn.execute(text('SELECT count(*) FROM insider_transactions WHERE is_current')).scalar()==0
    assert conn.execute(text('SELECT count(*) FROM insider_imports')).scalar()==5


def test_failed_replacement_rolls_back_and_keeps_other_months(local_db):
    from sqlalchemy import text
    rows=records();loader.load_window(START,END,rows,sources(rows))
    bad=dict(rows[0],occurrence=0)
    with pytest.raises(Exception):loader.load_window(START,END,[bad],sources([bad]))
    assert local_db.execute(text('SELECT count(*) FROM insider_transactions WHERE is_current')).scalar()==1
    loader.load_window(date(2026,8,1),date(2026,8,31),[],sources([]))
    assert local_db.execute(text('SELECT count(*) FROM insider_transactions WHERE is_current')).scalar()==1


def test_stale_response_cannot_overwrite_newer_snapshot(local_db):
    from sqlalchemy import text
    rows=records();loader.load_window(START,END,rows,sources(rows))
    with pytest.raises(ValueError,match='Newer snapshot'):
        loader.load_window(START,END,[],sources([],datetime.now(timezone.utc)-timedelta(days=1)))
    assert local_db.execute(text('SELECT count(*) FROM insider_transactions WHERE is_current')).scalar()==1


def test_overlapping_entity_aliases_merge_without_losing_source_multiplicity(local_db):
    from sqlalchemy import text
    rows=records(count=2)+records(count=2,status='NV')
    for row in rows[2:]:row['company_name']='FORMER COMPANY NAME'
    assert loader.load_window(START,END,rows,sources(rows))==2
    stored=local_db.execute(text('SELECT entity_status,source_entities FROM insider_transactions WHERE is_current')).all()
    assert len(stored)==2
    assert all(r.entity_status=='VI' and len(r.source_entities)==2 for r in stored)


def test_daily_plan_rechecks_recent_months_and_repairs_historical_holes(monkeypatch):
    from src.etl.insiders import downloader as module
    monkeypatch.setattr(module,'today_chile',lambda:date(2026,10,13))
    session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
    session.execute.return_value.all.return_value=[(date(2020,1,1),date(2020,1,31)),(date(2026,7,1),date(2026,7,31))]
    monkeypatch.setattr(module,'SessionLocal',lambda:session)
    d=InsidersDownloader();load=Mock();monkeypatch.setattr(d,'_load',load);d.run()
    windows=load.call_args.args[0]
    assert (date(2020,1,1),date(2020,1,31)) not in windows
    assert (date(2020,2,1),date(2020,2,29)) in windows
    assert (date(2026,7,1),date(2026,7,31)) in windows
    assert windows[-1]==(date(2026,10,1),date(2026,10,13))


def test_first_day_refreshes_full_history(monkeypatch):
    from src.etl.insiders import downloader as module
    monkeypatch.setattr(module,'today_chile',lambda:date(2026,11,1))
    d=InsidersDownloader();backfill=Mock();monkeypatch.setattr(d,'backfill',backfill);d.run()
    backfill.assert_called_once_with(date(2020,1,1),date(2026,11,1))


def test_insiders_job_registered_without_starting_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from src.scheduler import register_jobs
    scheduler=BackgroundScheduler(timezone='America/Santiago')
    register_jobs(scheduler)
    job=scheduler.get_job('insiders_transactions')
    assert job is not None and job.max_instances==1 and job.coalesce
    assert 'hour=\'8\'' in str(job.trigger) and 'minute=\'45\'' in str(job.trigger)
    assert not scheduler.running


def test_api_filters_dates_labels_and_preserves_exact_amounts(local_db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import insiders as api
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    local_db.execute(text('CREATE TEMP TABLE emisores (rut text PRIMARY KEY, razon_social text) ON COMMIT DROP'))
    local_db.execute(text("INSERT INTO emisores VALUES ('22222222','CORREDORA DE PRUEBA')"))
    rows=records();loader.load_window(START,END,rows,sources(rows))
    monkeypatch.setattr(api,'SessionLocal',lambda:Session(bind=local_db,join_transaction_mode='create_savepoint'))
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        response=client.get('/insiders/transactions',params={'company_rut':'12345678','date_from':'2026-09-01','date_to':'2026-09-30'})
        assert response.status_code==200,response.text
        data=response.json();assert data['total']==1
        row=data['rows'][0];assert row['units']=='10000000000000.25'
        assert row['broker_name']=='CORREDORA DE PRUEBA'
        assert row['broker_rut']=='22222222' and row['broker_name_source']=='SII'
        assert row['relationship_code_label']=='Director'
        assert 'raw_cells' not in row
        assert client.get('/insiders/transactions',params={'date_field':'communication','date_to':'2026-09-30'}).json()['total']==0
        assert client.get('/insiders/transactions',params={'offset':99}).json()['rows']==[]
        assert client.get('/insiders/transactions',params={'date_from':'2026-10-01','date_to':'2026-09-01'}).status_code==422
        assert client.get('/insiders/transactions',params={'date_field':'malicious'}).status_code==422
        assert client.get('/insiders/transactions',params={'search':"%'; DROP TABLE insider_transactions; --"}).json()['total']==0
        assert client.get('/insiders/codes').json()['codes']['security_type']['ACC']=='Acciones'
        coverage=client.get('/insiders/coverage').json()
        assert coverage['summary']['disclosures']==1 and len(coverage['windows'])==1


def test_malformed_observation_markup_does_not_swallow_amendment_cell():
    cells=CELLS.copy();cells[27]='Observación <s <'
    row=loader.parse_html(page(cells),'VI',START,END)[0]
    assert row['is_amendment'] is False
    assert row['observations']=='Observación'


def test_explorer_combined_filters_order_counts_and_empty_pages(local_db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import insiders_explorer as api
    from sqlalchemy import text
    local_db.execute(text('CREATE TEMP TABLE emisores (rut text PRIMARY KEY, razon_social text) ON COMMIT DROP'))
    local_db.execute(text("INSERT INTO emisores VALUES ('22222222','CORREDORA DE PRUEBA')"))
    a=records(count=2)
    cells=CELLS.copy();cells[0]='87654321';cells[2]='22222222';cells[9]='E';cells[21]='10';cells[7]='29/09/2026'
    b=records(cells)
    cells[0]='12345678';cells[2]='33333333';cells[21]=''
    c=records(cells)
    rows=a+b+c+records(status='NV',count=2);loader.load_window(START,END,rows,sources(rows))
    monkeypatch.setattr(api,'_read',lambda sql,params=None:[dict(r) for r in local_db.execute(text(sql),params or {}).mappings()])
    monkeypatch.setattr(api,'owner_index',lambda bucket:{'11111111':{'last_period':date(2026,6,30)}})
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        def get(**kw):
            r=client.get('/insiders/explorer',params=kw);assert r.status_code==200,r.text;return r.json()
        data=get(sort='amount',direction='asc')
        assert data['total']==4
        assert all(r['broker_name']=='CORREDORA DE PRUEBA' for r in data['rows'])
        assert [r['amount_clp'] for r in data['rows']]==['10','3200000000000','3200000000000',None]
        assert data['summary']['acquisitions']==2 # Source duplicates are disclosures, not net trades.
        assert data['monthly']==[{'month':'2026-09-01','reports':4,'acquisitions':2,'disposals':2,'other':0}]
        assert get(company_ruts=['12345678','87654321'],informant_ruts=['11111111','22222222'])['total']==3
        assert get(company_ruts=['12345678'],informant_ruts=['22222222'])['total']==0
        owner=get(owner_only=True);assert owner['total']==2
        assert owner['rows'][0]['owner_period']=='2026-06-30'
        assert owner['rows'][0]['units']=='10000000000000.25'
        assert get(offset=99)['rows']==[] and get(offset=99)['total']==4
        assert get(date_field='communication',date_to='2026-09-30')['total']==0
        assert get(search='%')['total']==0
        assert get(entity_status='NV')['total']==2 # Both source lists preserved for merged disclosures.
        assert client.get('/insiders/explorer',params={'date_from':'2026-10-01','date_to':'2026-09-01'}).status_code==422
        assert client.get('/insiders/explorer',params={'company_ruts':['123']*51}).status_code==422
        assert client.get('/insiders/explorer',params={'sort':'amount; DROP TABLE'}).status_code==422


def test_insider_connections_use_exact_rut_and_same_quarter_without_future_holdings(local_db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import insiders_explorer as api
    from sqlalchemy import text
    local_db.execute(text('CREATE TEMP TABLE aportantes_fi (run_fondo text,periodo date,rut text,pct_propiedad numeric) ON COMMIT DROP'))
    local_db.execute(text('CREATE TEMP TABLE fondos_inversion (run_fondo text,razon_social text,administrador text,moneda text,vigente boolean) ON COMMIT DROP'))
    local_db.execute(text('CREATE TEMP TABLE valores_cuota_fi (run_fondo text) ON COMMIT DROP'))
    local_db.execute(text("INSERT INTO aportantes_fi VALUES ('one','2026-03-31','0011.111.111-1',12.5),('two','2026-06-30','11111111',15),('wrong','2026-06-30','11111112',50),('future','2026-09-30','11111111',90),('unknown','2026-06-30','0',100),('invalid','2026-06-30','11111111',101),('malformed','2026-06-30','X11111111',50)"))
    local_db.execute(text("INSERT INTO fondos_inversion VALUES ('two','Fund two','AGF','CLP',true)"))
    local_db.execute(text("INSERT INTO valores_cuota_fi VALUES ('two')"))
    monkeypatch.setattr(api,'_read',lambda sql,params=None:[dict(r) for r in local_db.execute(text(sql),params or {}).mappings()])
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        r=client.get('/insiders/connections',params={'rut':'11111111','as_of':'2026-08-01'});assert r.status_code==200,r.text
        data=r.json();assert data['period']=='2026-06-30'
        by_run={r['run']:r for r in data['funds']};assert set(by_run)=={'two','invalid'}
        assert by_run['two']['weight']=='15' and by_run['two']['can_open']
        assert by_run['invalid']['weight'] is None and not by_run['invalid']['can_open']
        earlier=client.get('/insiders/connections',params={'rut':'11111111','as_of':'2026-04-01'}).json()
        assert [r['run'] for r in earlier['funds']]==['one']
        assert client.get('/insiders/connections',params={'rut':'0'}).status_code==422
        assert client.get('/insiders/connections',params={'rut':'11111111','as_of':'2020-01-01'}).json()['funds']==[]


def test_broker_names_use_one_exact_lookup_and_preserve_unknowns():
    from src.api.insiders import _with_broker_names
    rows=[{'broker_rut':v} for v in ['96571220','96.571.220-8','096571220','99999999',None,'0','corredor 96571220','96.57.1220']]
    read=Mock(return_value=[{'rut':'96571220','razon_social':'BANCHILE CORREDORES DE BOLSA S A'}])
    result=_with_broker_names(rows,read)
    read.assert_called_once()
    assert read.call_args.args[1]=={'ruts':['96571220','99999999']}
    assert len(result)==8
    assert [r['broker_name'] for r in result[:3]]==['BANCHILE CORREDORES DE BOLSA S A']*3
    assert result[1]['broker_rut']=='96.571.220-8'
    assert all(r['broker_name'] is None and r['broker_name_source'] is None for r in result[3:])
    read.reset_mock()
    assert _with_broker_names([],read)==[]
    assert _with_broker_names([{'broker_rut':None}],read)[0]['broker_name'] is None
    read.assert_not_called()
