"""Calendar ranking boundaries and daily-fact accounting (read-only DB fixtures)."""
from datetime import date
from importlib import import_module
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

rankings = import_module('src.api.industry_workspace.rankings')
daily = import_module('src.api.industry_workspace.nnm_daily')
api = import_module('src.api.industry_workspace.router')


def test_calendar_leap_year_partial_month_and_no_future_windows():
    windows = rankings.calendar_windows(date(2024,3,12))
    assert [w['key'] for w in windows] == ['YTD','01','02','03']
    assert windows[0]['start'] == date(2024,1,1)
    assert windows[2]['end'] == date(2024,2,29)
    assert windows[3]['end'] == date(2024,3,12) and windows[3]['truncated']
    assert not windows[2]['truncated']
    assert len(rankings.calendar_windows(date(2026,9,24),2025)) == 13
    assert rankings.calendar_windows(date(2026,9,24),2027) == []


def test_calendar_scans_each_kind_once_with_bounded_months_and_common_cutoff(monkeypatch):
    calls=[]
    def rows(sql,params=None):
        return [dict(date=date(2026,9,24)),dict(date=date(2026,9,20))] if 'MAX(latest_data_date)' in sql else []
    monkeypatch.setattr(api,'rows',rows)
    monkeypatch.setattr(rankings,'flow_data',lambda *args: calls.append(args) or [])
    rankings.load_nnm_periods.cache_clear()
    result=rankings.load_nnm_periods('all','CLP',None,None,0,'calendar')
    assert len(calls)==2
    assert result['end']==date(2026,9,20)
    assert all(c[2]==date(2026,1,1) and c[3]==date(2026,9,20) for c in calls)
    assert calls[0][4][1]==('01',date(2026,1,1),date(2026,1,31))
    assert calls[0][4][-1]==('09',date(2026,9,1),date(2026,9,20))
    rankings.load_nnm_periods('all','CLP',None,date(2026,9,20),0,'calendar',2025)
    assert calls[-1][3]==date(2025,12,31)
    assert len(calls[-1][4])==13


@pytest.mark.parametrize('params',[
    {'window':'calendar','year':2019}, {'window':'calendar','year':2099},
    {'window':'recent','year':2025},
    {'window':'calendar','from_date':'2026-01-10','to_date':'2026-02-10'},
])
def test_invalid_calendar_inputs_fail_before_query(params):
    app=FastAPI();app.include_router(rankings.router)
    assert TestClient(app).get('/rankings/nnm-periods',params=params).status_code==422


@pytest.mark.parametrize('kind',['fm','fi'])
def test_bounded_months_reconcile_with_standalone_queries_and_ytd(monkeypatch,kind):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':
        pytest.skip('Enable local read-only database fixtures')
    from src.db.engine import SessionLocal
    if kind=='fm':
        fixtures="""WITH fondo_mutuo AS (
          SELECT '1'::text run_fondo,'Test'::text nombre_fondo,NULL::date fecha_termino_operaciones
        ), categoria_fm_effective AS (
          SELECT '1'::text run_fondo,'Debt'::text nombre_cat,DATE '2020-01-01' periodo
        ), mv_nnm_daily_fm AS (
          SELECT '1'::text run_fondo,'CLP'::text currency,true has_nav,1 observations,
            CASE WHEN reported IS NULL THEN 0 ELSE 1 END reported_observations,
            reported aportes,0::numeric rescates,fecha,reported
          FROM (VALUES (DATE '2024-01-31',100::numeric),(DATE '2024-02-01',200),
            (DATE '2024-02-29',0),(DATE '2024-03-01',NULL),(DATE '2024-03-02',-10)) d(fecha,reported)
        ), fm_flow_adjustments AS (
          SELECT '1'::text target_run_fondo,'CLP'::text currency,'confirmed'::text status,event_date,amount
          FROM (VALUES (DATE '2024-01-31',20::numeric),(DATE '2024-02-01',50)) a(event_date,amount)
        ), """
    else:
        fixtures="""WITH fondos_inversion AS (
          SELECT '1'::text run_fondo,'Test'::text razon_social,true vigente
          UNION ALL SELECT '2','Inactive',false
        ), categoria_fi_effective AS (
          SELECT '1'::text run_fondo,'Debt'::text nombre_cat,DATE '2020-01-01' periodo
        ), v_nnm_daily_fi_continuous AS (
          SELECT '1'::text run_fondo,'CLP'::text currency,true has_nav,fecha,estimate,previous_snapshot,exclusion
          FROM (VALUES
            (DATE '2024-01-31',100::numeric,DATE '2024-01-30',NULL::text),
            (DATE '2024-02-01',200,DATE '2024-01-31',NULL),
            (DATE '2024-02-20',999,DATE '2024-01-20','gap'),
            (DATE '2024-02-29',0,DATE '2024-02-28',NULL),
            (DATE '2024-03-01',NULL,DATE '2024-02-29','incomparable_nav'),
            (DATE '2024-03-02',-10,DATE '2024-03-01',NULL)
          ) d(fecha,estimate,previous_snapshot,exclusion)
          UNION ALL SELECT '2','CLP',true,DATE '2024-02-01',99999,DATE '2024-01-31',NULL
          UNION ALL SELECT '1','USD',true,DATE '2024-02-01',88888,DATE '2024-01-31',NULL
        ), """
    def rows(sql,params):
        with SessionLocal() as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            return [dict(r) for r in session.execute(text(fixtures+sql.removeprefix('WITH ')),params).mappings()]
    monkeypatch.setattr(api,'rows',rows)
    start,end=date(2024,1,1),date(2024,3,2)
    windows=rankings.calendar_windows(end)
    results=daily.flow_data(kind,'CLP',start,end,[(w['key'],w['start'],w['end']) for w in windows])
    assert all(r['run']=='1' for r in results)
    by_period={r['period']:r for r in results}
    for w in windows:
        standalone=daily.flow_data(kind,'CLP',w['start'],w['end'])[0]
        assert {k:v for k,v in by_period[w['key']].items() if k!='period'}=={k:v for k,v in standalone.items() if k!='period'}
    net=lambda r:r['reported']-r['migrations']
    assert net(by_period['YTD'])==sum(net(by_period[key]) for key in ('01','02','03'))
    assert net(by_period['01'])==(80 if kind=='fm' else 100)
    assert net(by_period['02'])==(150 if kind=='fm' else 200)
    assert net(by_period['03'])==-10
    assert by_period['03']['reported_observations']==1
