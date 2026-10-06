from datetime import date
from importlib import import_module

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

m=import_module('src.api.industry_workspace.rankings')
api=import_module('src.api.industry_workspace.router')
ts=import_module('src.api.industry_workspace.administrator_timeseries')
fi=import_module('src.api.industry_workspace.investment_flows')


def test_returns_match_series_currency_at_calculation_date_and_exclude_quality_flags(monkeypatch):
    calls=[]
    def rows(sql,params):
        calls.append((sql,params))
        return [dict(run='1',serie=s,date=date(2026,9,24),name='Fund',admin='AGF',
                     category='Debt',category_type='Deuda',category_group='Nacional',
                     suspicious=flag,**{c:v for c in m.PERIOD_COLUMNS.values()})
                for s,flag,v in [('A',False,0),('B',True,999),('C',False,None)]]
    monkeypatch.setattr(api,'rows',rows);m.load_returns.cache_clear()
    result=m.load_returns('fm','USD',0)
    assert result['excluded_suspicious']==1
    assert [r['serie'] for r in result['rows']]==['A','C']
    assert result['rows'][0]['values']['1D']==0
    assert result['rows'][1]['values']['1D'] is None
    sql,p=calls[0]
    assert 'n.fecha=r.fecha_calculo' in sql and 'n.serie IS NOT DISTINCT FROM r.serie' in sql
    assert 'n.moneda' in sql and p['currency']=='USD'
    assert 'categoria_fm_effective' in sql


@pytest.mark.parametrize('kind',['fm','fi'])
def test_market_queries_do_not_require_admin_and_keep_old_scoped_path(monkeypatch,kind):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,p:calls.append((sql,p)) or [])
    query=ts.flow_period_data if kind=='fm' else fi.flow_period_data
    query('PEN',None,date(2026,9,24),None,[('ranking',date(2026,9,1))])
    assert '=:admin' not in calls[-1][0]
    query('PEN','AGF',date(2026,9,24),None,[('ranking',date(2026,9,1))])
    assert '=:admin' in calls[-1][0] and calls[-1][1]['admin']=='AGF'


def test_nnm_latest_data_anchor_and_migration_adjustment_preserve_missing(monkeypatch):
    def rows(sql,p=None):
        if 'MAX(latest_data_date)' in sql:return [dict(date=date(2026,9,24))]
        return [dict(run=run,admin='AGF',category_type='Debt',category_group='Local') for run in ('1','2','3')]
    calls=[]
    def flows(currency,admin,end,category,periods):
        calls.append((currency,admin,end,periods))
        return [dict(run=run,name=run,category='Debt',reported=r,migrations=a,
                     observations=2,reported_observations=0 if r is None else 2,
                     first_date=date(2026,9,24),last_date=date(2026,9,24))
                for run,r,a in [('1',50,10),('2',0,0),('3',None,None)]]
    monkeypatch.setattr(api,'rows',rows);monkeypatch.setattr(m,'flow_period_data',flows)
    m.load_nnm.cache_clear()
    r=m.load_nnm('fm','CLP','YTD',None,None,0)
    assert calls==[('CLP',None,date(2026,9,24),[('ranking',date(2026,1,1))])]
    assert [v['net'] for v in r['rows']]==[40,0,None]
    assert r['methodology']=='reported_external'


def test_long_windows_partition_funds_without_splitting_date_history(monkeypatch):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda *args:[dict(run=str(i)) for i in range(165)])
    def query(currency,admin,end,category,periods,fund_runs):
        calls.append((currency,admin,end,category,periods,fund_runs))
        return [dict(run=run) for run in fund_runs]
    monkeypatch.setattr(m,'fi_flow_period_data',query)
    start,end=date(2021,9,25),date(2026,9,24)
    result=m.flow_data('fi','USD',start,end)
    assert len(result)==165 and len({r['run'] for r in result})==165
    assert len(calls)==3 and max(len(c[-1]) for c in calls)==80
    assert all(c[:5]==('USD',None,end,None,[('ranking',start)]) for c in calls)


@pytest.mark.parametrize('params',[
    {'from_date':'2026-01-01'},
    {'from_date':'2026-01-02','to_date':'2026-01-01'},
    {'to_date':'2099-01-01'}, {'period':'bad'}, {'kind':'all'}, {'currency':'bad'},
])
def test_invalid_nnm_requests_fail_before_query(params):
    app=FastAPI();app.include_router(m.router)
    assert TestClient(app).get('/rankings/nnm',params=params).status_code==422
