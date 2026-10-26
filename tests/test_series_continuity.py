from datetime import date
from decimal import Decimal
import hashlib
import json
import os

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from src.api import series_continuity as m


def link(source='A', target='O', day='2026-01-05', ratio=1, event='rename'):
    return m.SeriesLink(source_serie=source,target_serie=target,effective_date=day,ratio=ratio,event_type=event,
        source_url='https://example.com/official-notice.pdf',reason='Confirmed whole series continuation')


def test_chain_order_dates_and_conversion():
    links=[link('B','A','2026-01-03',2,'conversion'),link()]
    m.validate_chain(links)
    history=m.series_history([x.model_dump() for x in links],'O',date(2026,1,6))
    assert [h['serie'] for h in history]==['B','A','O']
    assert history[0]['end']==date(2026,1,2)
    assert history[1]['ratio']=='2'
    assert m.series_history([], 'A')==[]


@pytest.mark.parametrize('links',[
    [link(),link('O','A','2026-01-06')],
    [link(),link('A','B','2026-01-06')],
    [link(),link('B','O','2026-01-06')],
    [link('B','A','2026-01-06'),link()],
])
def test_invalid_graphs(links):
    with pytest.raises(HTTPException):m.validate_chain(links)


@pytest.mark.parametrize('kwargs',[{'ratio':0},{'ratio':'NaN'},{'ratio':2},{'source':'O'},{'day':'2099-01-01'}])
def test_bad_inputs(kwargs):
    with pytest.raises(ValidationError):link(**kwargs)


def test_admin_requires_existing_editor_auth(monkeypatch):
    monkeypatch.setenv('CLASSIFICATION_ADMIN_KEYS',json.dumps({'editor':hashlib.sha256(b'test-key').hexdigest()}))
    app=FastAPI();app.include_router(m.router);client=TestClient(app)
    payload=dict(expected_version=0,reason='Official notice reviewed',links=[])
    for method,path in [('get','/admin/fi/123'),('put','/admin/fi/123'),('post','/admin/fi/123/preview')]:
        response=getattr(client,method)('/series-continuity'+path,**({} if method=='get' else {'json':payload}))
        assert response.status_code==401
    assert client.put('/series-continuity/admin/fi/123',headers={'Authorization':'Bearer test-key'},
        json=dict(payload,actor='spoofed')).status_code==422


@pytest.fixture
def db(monkeypatch):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':pytest.skip('Enable local transactional fixtures')
    from src.db.engine import engine
    from sqlalchemy.orm import Session
    assert engine.url.host in ('localhost','127.0.0.1','::1')
    connection=engine.connect();outer=connection.begin()
    session=Session(bind=connection,join_transaction_mode='create_savepoint')
    run='9999000718'
    session.execute(text("INSERT INTO fondos_inversion(run_fondo,razon_social,vigente) VALUES(:run,'Series test',true)"),dict(run=run))
    for serie,day,nav,aum in [('B','2025-12-30',100,1000),('B','2025-12-31',100,1000),
        ('B','2026-01-01',100,1000),('B','2026-01-02',100,1000),('A','2026-01-03',50,1000),
        ('O','2026-01-05',55,1100),('O','2026-01-06',56,1120)]:
        session.execute(text('''INSERT INTO valores_cuota_fi(run_fondo,serie,fecha,valor_libro,patrimonio_neto,moneda)
          VALUES(:run,:serie,:day,:nav,:aum,'$$')'''),dict(run=run,serie=serie,day=day,nav=nav,aum=aum))
    for serie,ticker in [('B','CFI-TESTCONT-B'),('B','CFITESTCONTB'),('A','CFITESTCONTA'),('O','CFITESTCONTO')]:
        session.execute(text('INSERT INTO nemotecnicos_fi(run_fondo,serie,nemotecnico) VALUES(:run,:serie,:ticker)'),dict(run=run,serie=serie,ticker=ticker))
    for ticker,day,amount,currency in [('CFITESTCONTB','2026-01-02',6,'$'),('CFITESTCONTA','2026-01-04',2,'$'),
        ('CFITESTCONTO','2026-01-07',999,'$'),('CFITESTCONTO','2026-01-06',999,'US$')]:
        session.execute(text("INSERT INTO dividendos(nemo,fec_lim,fec_pago,val_acc,moneda) VALUES(:ticker,:day,:day,:amount,:currency)"),dict(ticker=ticker,day=day,amount=amount,currency=currency))
    try:yield session,run
    finally:session.close();outer.rollback();connection.close()


def apply_chain(session,run):
    links=[link('B','A','2026-01-03',2,'conversion'),link()]
    return m.apply_edit(session,run,m.LinkEdit(expected_version=0,reason='Official notice reviewed',links=links),'test-editor')


def test_linked_history_returns_distributions_nnm_and_original_preservation(db,monkeypatch):
    session,run=db
    before=session.execute(text('SELECT sum(valor_libro),sum(patrimonio_neto),count(*) FROM valores_cuota_fi WHERE run_fondo=:run'),dict(run=run)).one()
    saved=apply_chain(session,run)
    assert saved['version']==1
    assert saved['links'][0]['ratio']=='2'
    reviewed=m.LinkEdit(expected_version=saved['version'],reason='Review unchanged definition',links=saved['links'])
    assert m.validate_observations(session,run,reviewed.links)
    nav=list(session.execute(text("SELECT * FROM fi_nav_history(:run,'O','2026-01-06') ORDER BY fecha"),dict(run=run)).mappings())
    assert [r['source_serie'] for r in nav]==['B','B','B','B','A','O','O']
    assert nav[0]['valor_libro']==50 and nav[0]['patrimonio_neto']==1000
    returns=session.execute(text("SELECT * FROM fi_linked_returns(:run,'O','2026-01-06')"),dict(run=run)).mappings().one()
    assert returns['r_ytd']==22 and returns['r_1w']==22
    assert returns['r_1d']==Decimal('1.8182') and returns['r_1y'] is None
    nnm=list(session.execute(text("SELECT * FROM v_nnm_daily_fi_continuous WHERE run_fondo=:run AND fecha IN ('2026-01-03','2026-01-05') ORDER BY fecha"),dict(run=run)).mappings())
    assert len(nnm)==2 and all(r['exclusion'] is None and r['estimate']==0 for r in nnm)
    assert session.execute(text('SELECT sum(valor_libro),sum(patrimonio_neto),count(*) FROM valores_cuota_fi WHERE run_fondo=:run'),dict(run=run)).one()==before
    # Both endpoints use the same SQL primitives; includes dividends on dates without NAV.
    from contextlib import contextmanager
    from src.api import investment_funds as fi
    @contextmanager
    def same_session():yield session
    monkeypatch.setattr(fi,'SessionLocal',same_session)
    chart=fi.get_investment_fund_return_series(run,None,'O',date(2025,12,31),date(2026,1,6),True)
    assert chart[-1].return_pct==22 and chart[-1].source_serie=='O'
    assert fi.get_investment_fund_return_series(run,None,'O',date(2025,12,31),date(2026,1,6),False)==[]
    assert fi.get_investment_fund_return_series(run,None,'O',date(2026,2,1),date(2026,2,2),True)==[]
    native=fi.get_investment_fund_return_series(run,None,'O',date(2026,1,5),date(2026,1,6),False)
    assert native[-1].return_pct==1.8182


def test_edit_version_audit_remove_and_validation(db):
    session,run=db
    saved=apply_chain(session,run)
    with pytest.raises(HTTPException) as stale:apply_chain(session,run)
    assert stale.value.status_code==409
    with pytest.raises(HTTPException):m.validate_observations(session,run,[link(day='2026-01-04')])
    removed=m.apply_edit(session,run,m.LinkEdit(expected_version=saved['version'],reason='Remove reviewed test links',links=[]),'second-editor')
    assert removed['version']==2 and removed['links']==[]
    audit=session.execute(text('SELECT actor,before_state,after_state FROM fi_series_link_audit WHERE run_fondo=:run ORDER BY version DESC'),dict(run=run)).mappings().all()
    assert audit[0]['actor']=='second-editor' and len(audit[0]['before_state'])==2 and audit[0]['after_state']==[]
    assert audit[1]['actor']=='test-editor'
    session.execute(text("UPDATE valores_cuota_fi SET moneda='PROM' WHERE run_fondo=:run AND serie='O'"),dict(run=run))
    with pytest.raises(HTTPException):m.validate_observations(session,run,[link()])


def test_detail_returns_use_each_series_latest_valid_date_and_keep_rankings_separate(db):
    from src.api.fi_detail_returns import latest_series_returns
    session, run = db
    # A fund need not exist in the shared-date ranking to have detail returns.
    assert not session.execute(text('SELECT 1 FROM mv_rentabilidad_fi WHERE run_fondo=:run'),dict(run=run)).first()
    result = {r['serie']: r for r in latest_series_returns(session, run)}
    assert result['B']['fecha_calculo'] == date(2026,1,2)
    assert result['A']['fecha_calculo'] == date(2026,1,3)
    assert result['O']['fecha_calculo'] == date(2026,1,6)
    assert result['O']['r_1d'] == Decimal('1.8182')
    assert result['O']['r_1y'] is None
    # Invalid and future observations cannot displace the last usable endpoint.
    for day, price, currency, aum in [('2099-01-01','999','$$',1000),
        ('2026-01-07','NaN','$$',1000),('2026-01-08','99',None,1000),
        ('2026-01-09','99','$$',-1),('2026-01-10','0','$$',1000)]:
        session.execute(text("""INSERT INTO valores_cuota_fi(run_fondo,serie,fecha,valor_libro,moneda,patrimonio_neto)
            VALUES(:run,'O',:day,:price,:currency,:aum)"""),dict(run=run,day=day,price=price,currency=currency,aum=aum))
    result = {r['serie']: r for r in latest_series_returns(session, run)}
    assert result['O']['fecha_calculo'] == date(2026,1,6)
    assert result['O']['r_1d'] == Decimal('1.8182')
    assert not session.execute(text('SELECT 1 FROM mv_rentabilidad_fi WHERE run_fondo=:run'),dict(run=run)).first()


def test_detail_returns_honor_confirmed_chain_and_missing_currency(db, monkeypatch):
    from contextlib import contextmanager
    from src.api import investment_funds as fi
    session, run = db
    apply_chain(session, run)
    @contextmanager
    def same_session(): yield session
    monkeypatch.setattr(fi, 'SessionLocal', same_session)
    detail = fi.get_investment_fund(run, None)
    assert [r.serie for r in detail.rentability] == ['O']
    r = detail.rentability[0]
    assert r.fecha_calculo == date(2026,1,6)
    assert r.r_ytd == 22  # converted predecessor prices and distributions
    assert r.r_1w == 22
    session.execute(text("UPDATE valores_cuota_fi SET moneda=NULL WHERE run_fondo=:run AND serie='A'"),dict(run=run))
    r = fi.get_investment_fund(run,None).rentability[0]
    assert r.r_1d == 1.8182
    assert r.r_1w is None and r.r_ytd is None
