"""FI accounting checks execute the real query against VALUES-only source fixtures.
Run locally with RUN_WORKSPACE_DB_TESTS=1; no database data is modified.
"""
import json
import runpy
from pathlib import Path
import os
from datetime import date
from importlib import import_module
import pytest
from sqlalchemy import text

fi=import_module('src.api.industry_workspace.investment_flows')
api=import_module('src.api.industry_workspace.router')
ts=import_module('src.api.industry_workspace.administrator_timeseries')


def nav(run, day, aum, price=1, series='A', currency='CLP'):
    return dict(run_fondo=run,fecha=f'2026-09-{day:02}',patrimonio_neto=aum,
                valor_libro=price,serie=series,moneda=currency)


@pytest.fixture(params=['source','daily'])
def estimate(monkeypatch,request):
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':
        pytest.skip('Enable RUN_WORKSPACE_DB_TESTS=1 for read-only PostgreSQL accounting fixtures')
    from src.db.engine import SessionLocal
    def run(data, currency='CLP', periods=None, admin='A', fund_runs=None):
        funds=[dict(run_fondo=r,razon_social=r,administrador='A',vigente=True)
               for r in sorted({v['run_fondo'] for v in data})]
        def rows(sql,params):
            if request.param=='daily':
                query=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/a7b8c9d0e1f2_add_daily_nnm.py'))['FI_SQL']
                sql='WITH mv_nnm_daily_fi AS ('+query+'), '+sql.removeprefix('WITH ')

            fixtures="""WITH valores_cuota_fi AS (
                SELECT * FROM jsonb_to_recordset(CAST(:nav AS jsonb)) AS t(
                  run_fondo text,fecha date,patrimonio_neto bigint,valor_libro numeric,serie text,moneda text)
              ), fondos_inversion AS (
                SELECT * FROM jsonb_to_recordset(CAST(:funds AS jsonb)) AS t(
                  run_fondo text,razon_social text,administrador text,vigente boolean)
              ), categoria_fi_effective AS (
                SELECT run_fondo,'Deuda'::text nombre_cat,DATE '2026-09-01' periodo FROM fondos_inversion
              ), """
            with SessionLocal() as session:
                session.execute(text('SET TRANSACTION READ ONLY'))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                return [dict(r) for r in session.execute(text(fixtures+sql.removeprefix('WITH ')),
                    dict(params,nav=json.dumps(data),funds=json.dumps(funds))).mappings()]
        monkeypatch.setattr(api,'rows',rows)
        periods=periods or [('test',date(2026,9,21))]
        if request.param=='daily':
            daily=import_module('src.api.industry_workspace.nnm_daily')
            result=daily.flow_data('fi',currency,min(s for _,s in periods),date(2026,9,24),periods)
            return [r for r in result if fund_runs is None or r['run'] in fund_runs]
        return fi.flow_period_data(currency,admin,date(2026,9,24),None,periods,fund_runs)
    return run


def test_price_return_is_not_flow_and_subscriptions_redemptions_are_signed(estimate):
    rows=estimate([nav('price',18,100),nav('price',21,110,1.1),
                   nav('inflow',18,100),nav('inflow',21,132,1.1),
                   nav('outflow',18,100),nav('outflow',21,88,1.1)])
    values={r['run']:float(r['reported']) for r in rows}
    assert values==pytest.approx(dict(price=0,inflow=22,outflow=-22),abs=1e-12)
    tree=ts.flow_tree(rows)
    assert tree['total']['net']==pytest.approx(0,abs=1e-12)
    assert tree['total']['aportes'] is None and tree['total']['rescates'] is None


def test_first_observation_invalid_nav_and_long_gaps_stay_unknown(estimate):
    rows=estimate([nav('new',23,1530700),nav('gap',14,100),nav('gap',23,150),
                   nav('invalid',18,100,0),nav('invalid',21,120),
                   nav('no_nav',21,None)])
    assert {r['run'] for r in rows}=={'new','gap','invalid'}
    assert all(r['reported'] is None and r['reported_observations']==0 for r in rows)
    assert next(r for r in rows if r['run']=='new')['missing_base']==1
    assert next(r for r in rows if r['run']=='gap')['gaps']==1
    assert next(r for r in rows if r['run']=='invalid')['incomparable_nav']==1


def test_currency_switch_does_not_bridge_previous_same_currency(estimate):
    rows=estimate([nav('switch',18,100,currency='PROM'),nav('switch',21,95000),
                   nav('switch',22,110,currency='US$'),nav('switch',23,120,currency='USD')],currency='USD')
    assert len(rows)==1 and float(rows[0]['reported'])==10
    assert rows[0]['observations']==2 and rows[0]['reported_observations']==1
    assert rows[0]['incomparable_nav']==1


def test_internal_series_transfer_nets_out_and_changed_series_set_is_excluded(estimate):
    rows=estimate([nav('transfer',18,100,series='A'),nav('transfer',18,100,series='B'),
                   nav('transfer',21,50,series='A'),nav('transfer',21,150,series='B'),
                   nav('changed',18,100,series='A'),nav('changed',18,100,series='B'),
                   nav('changed',21,200,series='A')])
    assert next(r for r in rows if r['run']=='transfer')['reported']==0
    changed=next(r for r in rows if r['run']=='changed')
    assert changed['reported'] is None and changed['series_changes']==1


def test_periods_include_only_their_flows_and_preserve_missing_cells(estimate):
    rows=estimate([nav('fund',18,100),nav('fund',21,110),nav('fund',23,125),
                   nav('new',23,100)],periods=[('1W',date(2026,9,18)),('1D',date(2026,9,24))])
    matrix=ts.build_flow_matrix(rows,[dict(key='1W'),dict(key='1D')])
    assert matrix['total']['values']['1W']['net']==25
    assert matrix['total']['values']['1D']['net'] is None
    new=next(f for c in matrix['categories'] for f in c['children'] if f['run']=='new')
    assert new['values']['1W']['net'] is None
    assert new['values']['1W']['excluded']['missing_base']==1
    assert '1D' not in new['values']


def test_market_currency_prefilter_and_fund_batches_preserve_full_history(estimate):
    data=[nav('switch',18,100,currency='PROM'),nav('switch',21,95000),
          nav('switch',22,110,currency='US$'),nav('switch',23,120,currency='USD'),
          nav('other',18,100,currency='USD'),nav('other',21,110,currency='USD'),
          nav('clp_only',18,100),nav('clp_only',21,120)]
    direct=estimate(data,currency='USD')
    market=estimate(data,currency='USD',admin=None)
    batches=[r for run in ['switch','other']
             for r in estimate(data,currency='USD',admin=None,fund_runs=[run])]
    order=lambda rows:sorted(rows,key=lambda r:r['run'])
    assert order(direct)==order(market)==order(batches)
    assert sum(r['reported'] for r in market)==20


def test_unchanged_empty_series_contribute_zero_without_excluding_active_series(estimate):
    data = [nav('pionero',18,100), nav('pionero',21,132,1.1)]
    for series in ['B','C','D']:
        data += [nav('pionero',18,0,0,series), nav('pionero',21,0,0,series)]
    result = estimate(data)[0]
    assert float(result['reported']) == pytest.approx(22)
    assert result['reported_observations'] == 1
    assert result['incomparable_nav'] == 0


@pytest.mark.parametrize('before,after', [
    ((0,0),(100,1)), ((100,1),(0,0)), ((None,0),(0,0)),
    ((0,None),(0,0)), ((100,0),(0,0)), ((0,0),(0,None)),
])
def test_empty_transition_and_incomplete_series_still_exclude_whole_snapshot(estimate,before,after):
    result = estimate([nav('fund',18,100),nav('fund',21,110),
                       nav('fund',18,*before,series='B'),nav('fund',21,*after,series='B')])[0]
    assert result['reported'] is None
    assert result['incomparable_nav'] == 1


def test_empty_series_currency_change_or_missing_date_cannot_pass_comparability(estimate):
    data = [nav('fund',18,100),nav('fund',21,110),
            nav('fund',18,0,0,'B'),nav('fund',19,0,0,'B',currency='USD'),
            nav('fund',21,0,0,'B')]
    result=estimate(data)[0]
    assert result['reported'] is None and result['incomparable_nav']==1


def test_all_empty_stable_series_are_observed_zero_not_missing(estimate):
    result=estimate([nav('empty',18,0,0),nav('empty',21,0,0)])[0]
    assert result['reported']==0 and result['reported_observations']==1


def test_history_before_query_lookback_is_missing_base_not_gap(estimate):
    result=estimate([nav('old',1,100),nav('old',21,150)])[0]
    assert result['reported'] is None and result['missing_base']==1 and result['gaps']==0


def test_old_series_base_cannot_be_bridged_by_materialized_history(estimate):
    result=estimate([nav('old',1,0,0,'B'),nav('old',18,100),nav('old',21,110),nav('old',21,0,0,'B')])[0]
    assert result['reported'] is None and result['series_changes']==1
