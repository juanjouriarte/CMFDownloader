from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api import fund_distributions as api


@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE nemotecnicos_fi (run_fondo TEXT,serie TEXT,nemotecnico TEXT)'))
        c.execute(text('CREATE TABLE dividendos (nemo TEXT,fec_lim TEXT,fec_pago TEXT,descrip_vc TEXT,val_acc REAL,moneda TEXT)'))
        c.execute(text('CREATE TABLE cartola_diaria (run_fondo TEXT,serie TEXT,fecha DATE,moneda TEXT,factor_reparto REAL)'))
        c.execute(text("INSERT INTO nemotecnicos_fi VALUES ('1','A','CFI-TEST'),('1','A','CFITEST'),('2','B','CFIOTHER')"))
        c.execute(text("""INSERT INTO dividendos VALUES
          ('CFITEST','2026-01-01','2026-01-05','DIVIDENDO DEF',2.5,'$'),
          ('CFI-TEST','2026-01-01','2026-01-05','DIVIDENDO DEF',2.5,'$'),
          ('CFITEST','2026-02-01',NULL,'REPARTO CAPITAL',1,'US$'),
          ('CFITEST','2026-03-01',NULL,'CANJE 2 X 1',0,NULL),
          ('CFITEST','2026-04-01',NULL,'EMIS PAGO',10,'$'),
          ('CFITEST','2026-05-01','2026-05-05','DIVIDENDO PROV',NULL,'$'),
          ('CFIOTHER','2026-01-01','2026-01-05','DIVIDENDO DEF',99,'$')"""))
        c.execute(text("""INSERT INTO cartola_diaria VALUES
          ('1','A','2026-01-01','$$',1),('1','A','2026-01-02','$$',1.05),
          ('1','A','2026-01-03','$$',0),('1','A','2026-01-04','$$',NULL),
          ('1','B','2026-01-02','US$',1.1),('2','A','2026-01-02','$$',2)"""))
    monkeypatch.setattr(api,'SessionLocal',lambda:Session(engine))
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        yield client


def test_fi_cash_events_deduplicate_tickers_and_exclude_capital_actions(client):
    r=client.get('/investment-funds/1/dividends')
    assert r.status_code==200
    rows=r.json()
    assert len(rows)==3
    assert [r['fecha'] for r in rows]==['2026-05-05','2026-02-01','2026-01-05']
    assert rows[0]['monto_por_cuota'] is None
    assert rows[1]['fecha_pago'] is None and rows[1]['moneda']=='US$'
    assert rows[1]['tipo']=='distribucion'
    assert rows[2]['monto_por_cuota']==2.5
    assert all(r['monto_total_clp'] is None for r in rows)
    assert client.get('/investment-funds/1/dividends?serie=B').json()==[]
    assert client.get('/investment-funds/1/dividends?from_date=2026-01-05&to_date=2026-01-05').json()==[rows[2]]


def test_fm_reparto_is_not_invented_cash_and_filters_are_exact(client):
    r=client.get('/mutual-funds/1/dividends?serie=A&from_date=2026-01-01&to_date=2026-01-04')
    assert r.status_code==200
    rows=r.json()
    assert len(rows)==1 and rows[0]['factor_reparto']==1.05
    assert rows[0]['monto_por_cuota'] is None and rows[0]['fecha_pago'] is None
    assert rows[0]['fuente']=='CMF cartola diaria'
    assert client.get('/mutual-funds/unknown/dividends').json()==[]


@pytest.mark.parametrize('kind',['mutual-funds','investment-funds'])
def test_date_validation(client,kind):
    assert client.get(f'/{kind}/1/dividends?from_date=2026-02-01&to_date=2026-01-01').status_code==422
    assert client.get(f'/{kind}/1/dividends?from_date=not-a-date').status_code==422
