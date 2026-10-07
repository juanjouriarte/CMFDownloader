"""Source and materialized FM accounting must agree, including live adjustments."""
import json
import os
import runpy
from datetime import date
from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy import text


def test_daily_fm_reconciles_series_counts_nulls_currency_and_adjustments(monkeypatch):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':
        pytest.skip('Enable local read-only database fixtures')
    from src.db.engine import SessionLocal
    api=import_module('src.api.industry_workspace.router')
    source=import_module('src.api.industry_workspace.administrator_timeseries')
    daily=import_module('src.api.industry_workspace.nnm_daily')
    query=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/a7b8c9d0e1f2_add_daily_nnm.py'))['FM_SQL']
    fixtures="""WITH fondo_mutuo AS (
      SELECT run_fondo,run_fondo nombre_fondo,'AGF'::text razon_social_administradora,
             NULL::date fecha_termino_operaciones,'CLP'::text moneda
      FROM (VALUES ('valid'),('invalid'),('empty'),('no_nav')) f(run_fondo)
    ), categoria_fm_effective AS (
      SELECT run_fondo,'Deuda'::text nombre_cat,DATE '2026-01-01' periodo FROM fondo_mutuo
    ), cartola_diaria AS (
      SELECT * FROM (VALUES
        ('valid',DATE '2026-09-21',100::numeric,20::numeric,100::numeric,'$$'),
        ('valid',DATE '2026-09-21',50,10,100,'CLP'),
        ('valid',DATE '2026-09-22',NULL,10,100,'0'),
        ('valid',DATE '2026-09-23',10,5,100,'CLP'),
        ('valid',DATE '2026-09-24',1000,0,100,'USD'),
        ('invalid',DATE '2026-09-21',-1,0,100,'CLP'),
        ('invalid',DATE '2026-09-22','NaN',0,100,'CLP'),
        ('empty',DATE '2026-09-21',0,0,0,'CLP'),
        ('no_nav',DATE '2026-09-21',100,0,NULL,'CLP')
      ) t(run_fondo,fecha,monto_aportado,monto_rescatado,patrimonio_neto,moneda)
    ), fm_flow_adjustments AS (
      SELECT * FROM jsonb_to_recordset(CAST(:adjustments AS jsonb)) AS t(
        target_run_fondo text,event_date date,amount numeric,currency text,status text)
    ), """
    adjustments=[dict(target_run_fondo='valid',event_date=f'2026-09-{day}',amount=amount,currency=currency,status=status)
                 for day,amount,currency,status in [(21,30,'CLP','confirmed'),(21,5,'CLP','auto_confirmed'),
                  (21,999,'CLP','candidate'),(21,999,'USD','confirmed'),(22,999,'CLP','confirmed'),(20,999,'CLP','confirmed')]]
    def rows(sql,params):
        if 'mv_nnm_daily_fm' in sql:
            sql='WITH mv_nnm_daily_fm AS ('+query+'), '+sql.removeprefix('WITH ')
        with SessionLocal() as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            return [dict(r) for r in session.execute(text(fixtures+sql.removeprefix('WITH ')),
                    dict(params,adjustments=json.dumps(adjustments))).mappings()]
    monkeypatch.setattr(api,'rows',rows)
    periods=[('range',date(2026,9,21)),('day',date(2026,9,23))]
    order=lambda rows:sorted(rows,key=lambda r:(r['run'],r['period']))
    def compare():
        raw=source.flow_period_data('CLP',None,date(2026,9,24),None,periods)
        facts=daily.flow_data('fm','CLP',date(2026,9,21),date(2026,9,24),periods)
        assert order(raw)==order(facts)
        return next(r for r in facts if r['run']=='valid' and r['period']=='range')
    result=compare()
    assert result['reported']==125 and result['migrations']==35
    assert result['observations']==4 and result['reported_observations']==3
    adjustments[0]['status']='rejected'
    assert compare()['migrations']==5  # Corrections remain live; no NAV refresh needed.
