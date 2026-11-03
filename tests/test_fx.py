from datetime import date as D
from decimal import Decimal as N
import importlib
import json
import os
import uuid
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from src.fx import Rates
from src.etl.fx.catalog import SERIES
from src.etl.fx.downloader import parse_series,fetch_series,BDEError
from src.api.fx import convert_aum,aggregate,flow_total


def payload(obs,code='F073.TCO.PRE.Z.D'):
 return {'Codigo':0,'Series':{'seriesId':code,'Obs':obs}}
def obs(day,value,status='OK'):
 return {'indexDateString':day,'value':value,'statusCode':status}
def parse(p):return parse_series(p,'F073.TCO.PRE.Z.D',D(2026,10,1),D(2026,10,31))

def test_parse_precision_missing_and_identity():
 rows=parse(payload([obs('01-10-2026','987.123456789'),obs('03-10-2026','NaN','ND')]))
 assert rows[0]['value']==N('987.123456789') and rows[1]['value'] is None
 for bad in [payload([],code='other'),payload([]),{'Codigo':-1,'Descripcion':'secret-token'},
  payload([obs('01-10-2026','0')]),payload([obs('01-10-2026','NaN')]),
  payload([obs('01-10-2026','-2')]),payload([obs('01-10-2026','1'),obs('01-10-2026','2')]),
  payload([obs('01-11-2026','1')])]:
  with pytest.raises(BDEError) as e:parse(bad)
  assert 'secret-token' not in str(e.value)

def test_request_errors_never_expose_token(monkeypatch):
 class Broken:
  def get(self,*a,**k):raise RuntimeError('https://host?token=secret-key')
 monkeypatch.setattr('src.etl.fx.downloader.time.sleep',lambda _:None)
 with pytest.raises(BDEError) as e:fetch_series(Broken(),'secret-key','series',D(2020,1,1),D(2020,1,2))
 assert 'secret-key' not in str(e.value) and e.value.__context__ is None

def test_catalog_units_unique_and_core_currencies():
 assert len(SERIES)==61
 assert len({r['currency'] for r in SERIES})==len(SERIES)
 assert len({r['series_code'] for r in SERIES})==len(SERIES)
 assert {'USD','EUR','COP','PEN','UF'}.issubset({r['currency'] for r in SERIES})
 assert not any(r['currency']=='CLP' for r in SERIES)
 assert all('.USD.N.O.D' in r['series_code'] for r in SERIES if r['unit_kind']=='per_usd')

def book():return Rates([{'currency':'USD','date':D(2026,10,2),'clp_per_unit':N(900),'series_code':'usd'},
 {'currency':'USD','date':D(2026,10,5),'clp_per_unit':N(1000),'series_code':'usd'}])

def test_asof_never_future_and_staleness():
 r=book()
 assert r.get('USD',D(2026,10,1)) is None
 assert r.get('USD',D(2026,10,4))['clp_per_unit']==900
 assert r.get('USD',D(2026,10,13)) is None
 assert r.get('GBP',D(2026,10,5)) is None
 assert r.get('CLP',D(2020,1,1))['clp_per_unit']==1

def test_consolidation_deduplicates_funds_and_suppresses_incomplete_total():
 rows=[dict(fund_type='fi',run_fondo='1',currency=c,data_date=D(2026,10,4),aum=N(v)) for c,v in [('CLP',1000),('USD',100),('EUR',10)]]
 converted,fx=convert_aum(rows,book());total=aggregate(converted)
 assert rows[1]['aum']==100 # Never overwrite native data.
 assert converted[1]['aum']==90000
 assert total['aum'] is None and total['covered_aum']==91000
 assert total['funds']==1 and total['missing_fx_funds']==1
 assert next(r for r in fx if r['currency']=='EUR')['aum_clp'] is None
 assert aggregate(converted[:2])['aum']==91000

def test_flow_uses_daily_rates_and_signed_adjustments():
 points=[dict(currency='USD',date=D(2026,10,2),amount=N(10)),dict(currency='USD',date=D(2026,10,5),amount=N(20)),dict(currency='USD',date=D(2026,10,2),amount=N(-5))]
 assert flow_total(points,book())['amount']==24500
 points.append(dict(currency='EUR',date=D(2026,10,5),amount=N(1)))
 assert flow_total(points,book())['amount'] is None
 assert flow_total([],book())['amount'] is None

@pytest.fixture
def db(monkeypatch):
 if os.getenv('RUN_FX_DB_TESTS')!='1':pytest.skip('Opt-in local database checks')
 from src.db.engine import engine
 assert engine.url.host in ('localhost','127.0.0.1','::1')
 schema='test_fx_'+uuid.uuid4().hex
 with engine.begin() as c:
  c.execute(text(f'CREATE SCHEMA {schema}'))
  c.execute(text(f'CREATE TABLE {schema}.fx_series (LIKE public.fx_series INCLUDING ALL)'))
  c.execute(text(f'CREATE TABLE {schema}.fx_observations (LIKE public.fx_observations INCLUDING ALL)'))
  definition=c.execute(text("SELECT pg_get_viewdef('public.fx_daily'::regclass,true)")).scalar()
  c.execute(text('SET LOCAL search_path TO '+schema))
  c.execute(text('CREATE VIEW fx_daily AS '+definition.replace('public.','')))
 eng=create_engine(engine.url,connect_args={'options':f'-c search_path={schema}'})
 sessions=sessionmaker(bind=eng)
 downloader=importlib.import_module('src.etl.fx.downloader')
 monkeypatch.setattr(downloader,'SessionLocal',sessions)
 try:yield sessions
 finally:
  eng.dispose()
  with engine.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))

def test_cross_rates_exact_date_and_upsert_missing(db):
 from src.etl.fx.downloader import seed_catalog
 seed_catalog()
 with db() as s:
  s.execute(text("INSERT INTO fx_observations(series_code,date,value,status) VALUES ('F073.TCO.PRE.Z.D','2026-10-02',900,'OK'),('F072.COP.USD.N.O.D','2026-10-02',4000,'OK'),('F072.EUR.USD.N.O.D','2026-10-03',0.9,'OK')"))
  rows=list(s.execute(text('SELECT currency,clp_per_unit FROM fx_daily')).all())
  assert dict(rows)=={'USD':N(900),'COP':N('.225')}
  s.execute(text("UPDATE fx_observations SET status='ND',value=NULL WHERE series_code='F073.TCO.PRE.Z.D'"))
  assert s.execute(text('SELECT count(*) FROM fx_daily')).scalar()==0

def test_import_idempotency_checkpoints_and_failure(db,monkeypatch):
 from src.etl.fx import downloader as d
 specs=[r for r in SERIES if r['currency'] in ('USD','EUR')]
 monkeypatch.setattr(d,'SERIES',specs);monkeypatch.setenv('BCCH_API_TOKEN','test-only')
 monkeypatch.setattr(d.time,'sleep',lambda _:None)
 def fetch(http,token,code,start,end):
  if 'EUR' in code:raise BDEError('Test failure')
  return [dict(series_code=code,date=D(2026,10,1),value=N(900),status='OK')]
 monkeypatch.setattr(d,'fetch_series',fetch)
 first=d.FXDownloader().run();second=d.FXDownloader().run()
 assert first.errors==second.errors==1 and first.downloaded==1
 with db() as s:
  assert s.execute(text('SELECT count(*) FROM fx_observations')).scalar()==1
  points=dict(s.execute(text('SELECT currency,checked_through FROM fx_series')).all())
  assert points['USD']==D(2026,10,1) and points['EUR'] is None


def test_monthly_query_aligns_currency_changes_and_uses_nav_dates(db,monkeypatch):
 api=importlib.import_module('src.api.fx')
 monkeypatch.setattr(api,'_monthly_history_is_current',lambda _:True)
 def query(sql,params):
  with db() as s:return [dict(r) for r in s.execute(text(sql),params).mappings()]
 monkeypatch.setattr(api,'_rows',query)
 monkeypatch.setattr(Rates,'load',classmethod(lambda cls,a,b:book()))
 with db() as s:
  for kind,identity,admin in [('fm','fondo_mutuo','razon_social_administradora'),('fi','fondos_inversion','administrador')]:
   s.execute(text(f'CREATE TABLE {identity}(run_fondo text,{admin} text)'))
   s.execute(text(f'CREATE TABLE categoria_{kind}_effective(run_fondo text,nombre_cat text,periodo date,tipo text,grupo text)'))
   s.execute(text(f'CREATE TABLE mv_industry_monthly_{kind}(run_fondo text,currency text,latest_data_date date,aum numeric,month date)'))
  s.execute(text("INSERT INTO fondos_inversion VALUES('1','Admin'),('2','Admin'),('3','Admin')"))
  s.execute(text("INSERT INTO mv_industry_monthly_fi VALUES('1','CLP','2026-10-02',1000,'2026-10-01'),('1','USD','2026-10-05',10,'2026-10-01'),('2','USD','2026-10-02',10,'2026-10-01'),('3','USD','2026-10-02',20,'2026-10-01'),('3','CLP','2026-10-05',NULL,'2026-10-01')"))
  s.commit()
 rows=api.consolidated_evolution(None,fund_type='fi',from_date=D(2026,10,1))
 assert len(rows)==1 and rows[0]['aum']==19000 and rows[0]['funds']==2
 assert rows[0]['first_fx_date']==D(2026,10,2)
 native=api.consolidated_evolution(None,fund_type='fi',from_date=D(2026,10,1),currency='USD')
 assert native[0]['aum']==20 and native[0]['funds']==2
 # Currency filtering must not resurrect the old CLP observation or the old
 # USD observation whose fund closes with missing CLP AUM.
 assert api.consolidated_evolution(None,fund_type='fi',from_date=D(2026,10,1),currency='CLP')==[]
