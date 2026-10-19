from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

flows=import_module('src.api.industry_workspace.fund_flows')
D=date(2026,1,1)


def source(d,serie='A',aportes=100,rescates=20,aum=1000):
    return dict(date=d,serie=serie,aportes=aportes,rescates=rescates,aum=aum)


@pytest.mark.parametrize('period,end,expected',[
    ('1D',date(2026,3,31),date(2026,3,31)),
    ('1W',date(2026,3,31),date(2026,3,25)),
    ('1M',date(2024,3,31),date(2024,3,1)),
    ('1A',date(2024,2,29),date(2023,3,1)),
    ('3A',date(2026,10,19),date(2023,10,20)),
    ('5A',date(2026,10,19),date(2021,10,20)),
    ('YTD',date(2026,1,1),date(2026,1,1)),
])
def test_period_boundaries(period,end,expected):
    assert flows.period_start(period,end)==expected


def test_series_reconcile_total_and_explicit_migration_not_allocated_to_series():
    rows=flows.normalize([source(D-timedelta(days=1),s,0,0) for s in ['A','B']]+
                         [source(D,s) for s in ['A','B']])
    result=flows.analyze(rows,{D:Decimal(50)},D,D)
    total=result['total']
    assert total['reported']==sum(r['net'] for r in result['series'])==160
    assert total['net']==110 and total['migrations']==50
    assert total['initial_aum']==2000 and total['rate']==Decimal('5.5')
    assert all(r['migrations']==0 for r in result['series'])
    assert total['ytd']['net']==total['monthly'][0]['net']==total['net']


def test_missing_values_zero_and_invalid_amounts_are_distinct():
    rows=flows.normalize([source(D,aportes=0,rescates=0),source(D+timedelta(days=1),aportes=None),
                         source(D+timedelta(days=2),aportes='NaN'),source(D+timedelta(days=3),rescates=-1)])
    assert [r['net'] for r in rows]==[0,None,None,None]
    total=flows.summary(rows,D,D+timedelta(days=3))
    assert total['net']==0 and total['valid_observations']==1
    assert total['rate'] is None and not total['complete']
    assert flows.summary([],D,D)['net'] is None


def test_gap_stops_cumulative_and_rate_but_does_not_erase_observed_amounts():
    records=flows.normalize([source(D-timedelta(days=1)),source(D),source(D+timedelta(days=2))])
    result=flows.analyze(records,{},D,D+timedelta(days=2))['total']
    assert result['net']==160 and result['rate'] is None
    assert result['days']==2 and result['expected_days']==3
    assert [p['net'] for p in result['timeline']]==[80,None,80]
    assert [p['cumulative_rate'] for p in result['timeline']]==[8,None,None]


def test_zero_or_stale_baseline_does_not_produce_rate():
    for baseline in [source(D-timedelta(days=1),aum=0),source(D-timedelta(days=9))]:
        r=flows.summary(flows.normalize([baseline,source(D)]),D,D)
        assert r['net']==80 and r['rate'] is None


def test_snapshot_uses_one_date_and_rejects_invalid_aum():
    records=flows.normalize([source(D-timedelta(days=2),'A',aum=900),source(D-timedelta(days=1),'A',aum=1000),source(D-timedelta(days=1),'B',aum=None),source(D)])
    assert flows.summary(records,D,D)['initial_aum'] is None


def test_monthly_windows_are_disjoint_and_ytd_stops_at_cutoff():
    start=date(2024,1,1);end=date(2024,3,5)
    records=flows.normalize([source(start+timedelta(days=i),aportes=1,rescates=0) for i in range(-1,(end-start).days+1)])
    total=flows.analyze(records,{},date(2024,3,1),end)['total']
    assert [r['net'] for r in total['monthly']]==[31,29,5]
    assert total['ytd']['net']==65 and total['net']==5


def test_previous_period_is_adjacent_same_length_and_requires_complete_data():
    records=flows.normalize([source(D+timedelta(days=i),aportes=20 if i<2 else 30,rescates=0) for i in range(4)])
    total=flows.analyze(records,{},D+timedelta(days=2),D+timedelta(days=3))['total']
    assert total['previous']['net']==40 and total['change']==20


@pytest.mark.parametrize('params',[{'from_date':'2026-01-01'},{'to_date':'2026-01-01'},
    {'from_date':'2019-01-01','to_date':'2026-01-01'},
    {'from_date':'2026-01-05','to_date':'2026-01-01'},{'period':'10A'},
    {'currency':'CLP;DROP TABLE'}])
def test_invalid_inputs_rejected_before_db(params):
    app=FastAPI();app.include_router(flows.router)
    assert TestClient(app).get('/fund-flows/fm/8011',params=params).status_code==422


def test_category_excludes_own_fund_other_currency_and_category_and_applies_adjustments(monkeypatch):
    import os
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':
        pytest.skip('Enable local read-only PostgreSQL fixtures')
    from sqlalchemy import text
    from src.db.engine import SessionLocal
    api=import_module('src.api.industry_workspace.router')
    fixture="""WITH fondo_mutuo AS (
      SELECT run_fondo,run_fondo nombre_fondo,'Admin'::text razon_social_administradora,
        NULL::date fecha_termino_operaciones,'CLP'::text moneda,
        CASE WHEN run_fondo='new' THEN DATE '2026-01-02' END fecha_inicio_operaciones
      FROM unnest(ARRAY['own','peer','other_category','usd','partial','no_base','new']) run_fondo
    ), categoria_fm_effective AS (
      SELECT run_fondo,CASE WHEN run_fondo='other_category' THEN 'Other' ELSE 'Debt' END nombre_cat,
        'Deuda'::text tipo,'Local'::text grupo,DATE '2020-01-01' periodo FROM fondo_mutuo
    ), mv_nnm_daily_fm AS (
      SELECT f.run_fondo,d.fecha,CASE WHEN f.run_fondo='usd' THEN 'USD' ELSE 'CLP' END currency,
        true has_nav,1 observations,CASE WHEN f.run_fondo='partial' AND d.fecha=DATE '2026-01-02' THEN 0 ELSE 1 END reported_observations,
        CASE WHEN f.run_fondo='partial' AND d.fecha=DATE '2026-01-02' THEN NULL ELSE 100::numeric END aportes,
        20::numeric rescates,CASE WHEN f.run_fondo='partial' AND d.fecha=DATE '2026-01-02' THEN NULL ELSE 80::numeric END reported
      FROM fondo_mutuo f CROSS JOIN (VALUES(DATE '2026-01-01'),(DATE '2026-01-02')) d(fecha)
      WHERE f.run_fondo<>'new' OR d.fecha>=f.fecha_inicio_operaciones
    ), fm_flow_adjustments AS (
      SELECT 'peer'::text target_run_fondo,'CLP'::text currency,DATE '2026-01-01' event_date,
        50::numeric amount,'confirmed'::text status
    ), cartola_diaria AS (
      SELECT run_fondo,'CLP'::text moneda,DATE '2025-12-31' fecha,1000::numeric patrimonio_neto
      FROM fondo_mutuo WHERE run_fondo NOT IN ('no_base','new')
    ), """
    def rows(sql,params):
        with SessionLocal() as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            return [dict(r) for r in session.execute(text(fixture+sql.removeprefix('WITH ')),params).mappings()]
    monkeypatch.setattr(api,'rows',rows)
    params=dict(run='own',currency='CLP',start=D,end=D+timedelta(days=1),category='Debt',tipo='Deuda',grupo='Local')
    peers={p['run']:p for p in flows.category_peers(params)}
    assert set(peers)=={'peer','partial','no_base','new'}
    assert peers['peer']['net']==110 and peers['peer']['rate']==11
    assert peers['partial']['net']==80 and peers['partial']['rate'] is None
    assert peers['no_base']['rate'] is None
    points=flows.category_timeline(params,['peer'],'daily',Decimal(1000))
    assert [p['net'] for p in points]==[30,80]
    assert points[-1]['cumulative_rate']==peers['peer']['rate']
    calendar=flows.category_calendar(params)
    assert calendar['ytd']['net']==430  # Includes no-base peers for absolute NNM.
    assert calendar['ytd']['funds']==4 and calendar['ytd']['known_funds']==4
    assert calendar['ytd']['days']==calendar['ytd']['expected_days']==7
    assert calendar['ytd']['pre_inception_days']==1
    assert calendar['ytd']['started_funds'][0]['date']==date(2026,1,2)
    assert calendar['monthly'][0]['net']==calendar['ytd']['net']
    assert not calendar['ytd']['complete']
    assert calendar['ytd']['migrations']==50


def test_category_calendar_ytd_equals_months_and_no_data_is_not_zero():
    records=[dict(run='1',month=date(2026,1,1),net=Decimal(0),reported=Decimal(5),migrations=Decimal(5),observations=31,valid_observations=31,days=31,first_date=date(2026,1,1),last_date=date(2026,1,31)),
             dict(run='1',month=date(2026,2,1),net=Decimal(-20),reported=Decimal(-20),migrations=Decimal(0),observations=28,valid_observations=28,days=28,first_date=date(2026,2,1),last_date=date(2026,2,28))]
    result=flows.calendar_totals(records,date(2026,3,5))
    assert [r['net'] for r in result['monthly']]==[0,-20,None]
    assert result['ytd']['net']==sum(r['net'] or 0 for r in result['monthly'])==-20
    assert result['monthly'][0]['complete']
    assert not result['monthly'][2]['complete']
    assert result['ytd']['days']==59 and result['ytd']['expected_days']==64
    assert len(result['monthly'])==3
    assert flows.calendar_totals([],D)['ytd']['net'] is None


def test_confirmed_midmonth_start_has_complete_reports_without_inventing_opening_nav():
    start=date(2026,7,1); inception=date(2026,7,6); end=date(2026,7,31)
    records=flows.normalize([source(inception+timedelta(days=i)) for i in range(26)])
    result=flows.analyze(records,{},start,end,inception)['total']
    assert result['complete'] and result['days']==result['expected_days']==26
    assert result['pre_inception_days']==5 and result['inception_date']==inception
    assert result['net']==26*80 and result['rate'] is None and result['change'] is None
    assert result['monthly'][6]['complete'] and result['ytd']['complete']
    assert result['monthly'][5]['expected_days']==0 and result['monthly'][5]['net'] is None
    # A missing day after inception remains a gap; null records still invalidate coverage.
    gap=flows.summary(records[1:],start,end,inception=inception)
    assert not gap['complete'] and gap['days']==25 and gap['expected_days']==26
    invalid=flows.normalize([source(inception,aportes=None)]+[source(inception+timedelta(days=i)) for i in range(1,26)])
    assert not flows.summary(invalid,start,end,inception=inception)['complete']
    # No official start: never infer inception from the first record in our DB.
    assert flows.summary(records,start,end)['expected_days']==31
    assert not flows.summary(records,start,end)['complete']
    # A later series starts within an older fund: its unverified start stays a gap.
    assert not flows.analyze(records,{},start,end,date(2020,1,1))['series'][0]['complete']


def test_registry_start_conflicting_with_earlier_reports_is_not_used_to_hide_dates():
    assert flows.confirmed_start(date(2026,7,6),date(2026,7,1)) is None
    assert flows.confirmed_start(None,date(2026,7,6)) is None
    assert flows.confirmed_start(date(2026,7,6),date(2026,7,7))==date(2026,7,6)


def test_category_start_adjustment_months_before_inception_and_ytd():
    def record(run,month,first,last,inception=None):
        days=(last-first).days+1
        return dict(run=run,name=run,month=month,net=Decimal(days),reported=Decimal(days),migrations=0,
                    observations=days,valid_observations=days,days=days,first_date=first,last_date=last,
                    inception_date=inception,history_start=inception or date(2020,1,1))
    records=[record('old',date(2026,1,1),date(2026,1,1),date(2026,1,31)),
             record('old',date(2026,2,1),date(2026,2,1),date(2026,2,28)),
             record('new',date(2026,2,1),date(2026,2,15),date(2026,2,28),date(2026,2,15))]
    result=flows.calendar_totals(records,date(2026,2,28))
    jan,feb=result['monthly']
    assert jan['funds']==jan['known_funds']==1 and jan['complete']
    assert jan['days']==jan['expected_days']==31
    assert feb['funds']==feb['known_funds']==2 and feb['complete']
    assert feb['days']==feb['expected_days']==42 and feb['pre_inception_days']==14
    assert result['ytd']['days']==result['ytd']['expected_days']==73
    assert result['ytd']['complete'] and result['ytd']['net']==jan['net']+feb['net']
    records[-1]['days']-=1
    assert not flows.calendar_totals(records,date(2026,2,28))['monthly'][1]['complete']
    records[-1]['inception_date']=None
    assert flows.calendar_totals(records,date(2026,2,28))['monthly'][1]['expected_days']==56
