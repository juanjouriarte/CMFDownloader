"""Execute FI return-view SQL with isolated, read-only source fixtures."""
from datetime import date, timedelta
from pathlib import Path
import json
import os
import runpy

import pytest
from sqlalchemy import text

migration = runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/d0e1f2a3b4c5_fi_returns_all_nav.py'))


def test_fi_return_migration_preserves_concurrent_refresh_indexes(monkeypatch):
    statements=[]
    monkeypatch.setattr(migration['op'],'execute',statements.append)
    migration['upgrade']()
    assert statements[0]=='DROP MATERIALIZED VIEW IF EXISTS mv_rentabilidad_fi'
    assert any('CREATE UNIQUE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_pk' in sql for sql in statements)
    assert len([sql for sql in statements if 'CREATE INDEX IF NOT EXISTS' in sql])==2
    statements.clear();migration['downgrade']()
    assert migration['PREVIOUS_FI_SQL'] in statements
    assert any('CREATE UNIQUE INDEX IF NOT EXISTS' in sql for sql in statements)


def test_returns_cover_non_rescatable_and_redeemable_with_exact_series_baselines_and_distributions():
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':
        pytest.skip('Enable local read-only PostgreSQL fixtures')
    from src.db.engine import SessionLocal
    end=date(2026,9,24)
    bases=[end-timedelta(days=d) for d in (1,7,30,365,1825)]+[date(2025,12,31)]
    funds=[dict(run_fondo=str(i),razon_social='Fund '+str(i),administrador='AGF',vigente=i!=3,rescatable=i==2) for i in range(1,8)]
    nav=[]
    def add(run,serie,day,price,currency='$$'):
        nav.append(dict(run_fondo=str(run),serie=serie,fecha=str(day),valor_libro=str(price),moneda=currency,patrimonio_neto=1000))
    for run in range(1,8):
        serie=None if run==7 else 'A'
        add(run,serie,end,'NaN' if run==4 else 110,'PROM' if run==5 else '$$')
        if run!=6:
            for day in bases:add(run,serie,day,100)
    add(1,'ZERO',end,0)
    add(1,'NEW',end,110)
    for day in bases:add(1,'NEW',day,0)
    add(1,'OLD',end-timedelta(days=2),100)
    add(1,'A',date(2099,1,1),999)  # Future source dates never advance the cutoff.
    dividends=[dict(nemo='CFI-TEST',fec_lim=str(day),val_acc=str(value),moneda=currency) for day,value,currency in [
        (end,5,'$'),(end-timedelta(days=1),2,'CLP'),(end-timedelta(days=7),3,'$$'),
        (end+timedelta(days=1),999,'$'),(end,999,'US$'),(end,'NaN','$')]]
    tickers=[dict(run_fondo='1',serie='A',nemotecnico=n) for n in ('CFI-TEST','CFITEST')]
    fixtures="""WITH fondos_inversion AS (
      SELECT * FROM jsonb_to_recordset(CAST(:funds AS jsonb)) AS f(run_fondo text,razon_social text,administrador text,vigente boolean,rescatable boolean)
    ), valores_cuota_fi AS (
      SELECT * FROM jsonb_to_recordset(CAST(:nav AS jsonb)) AS n(run_fondo text,serie text,fecha date,valor_libro numeric,moneda text,patrimonio_neto bigint)
    ), nemotecnicos_fi AS (
      SELECT * FROM jsonb_to_recordset(CAST(:tickers AS jsonb)) AS t(run_fondo text,serie text,nemotecnico text)
    ), dividendos AS (
      SELECT * FROM jsonb_to_recordset(CAST(:dividends AS jsonb)) AS d(nemo text,fec_lim date,val_acc numeric,moneda text)
    ), """
    query=migration['FI_SQL'].split('AS\nWITH ',1)[1]
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        session.execute(text("SET LOCAL statement_timeout='10s'"))
        rows=[dict(r) for r in session.execute(text(fixtures+query),dict(funds=json.dumps(funds),nav=json.dumps(nav),tickers=json.dumps(tickers),dividends=json.dumps(dividends))).mappings()]
    result={(r['run_fondo'],r['serie']):r for r in rows}
    assert set(result)=={('1','A'),('1','NEW'),('2','A'),('5','A'),('6','A'),('7',None)}
    assert all(r['fecha_calculo']==end for r in rows)
    assert [result['1','A'][p] for p in ('r_1d','r_1w','r_1m','r_1y','r_5y','r_ytd')]==[15,17,20,20,20,20]
    assert result['2','A']['r_1d']==10  # Existing redeemable accounting retained.
    assert result['7',None]['r_1d']==10  # Exact nullable series identity supported.
    for key in [('1','NEW'),('5','A'),('6','A')]:
        assert all(result[key][p] is None for p in ('r_1d','r_1w','r_1m','r_1y','r_5y','r_ytd'))
