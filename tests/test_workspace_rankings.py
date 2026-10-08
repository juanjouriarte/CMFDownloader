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
    def flows(kind,currency,start,end,periods=None):
        calls.append((kind,currency,start,end,periods))
        return [dict(run=run,name=run,category='Debt',reported=r,migrations=a,
                     observations=2,reported_observations=0 if r is None else 2,
                     first_date=date(2026,9,24),last_date=date(2026,9,24))
                for run,r,a in [('1',50,10),('2',0,0),('3',None,None)]]
    monkeypatch.setattr(api,'rows',rows);monkeypatch.setattr(m,'flow_data',flows)
    m.load_nnm.cache_clear()
    r=m.load_nnm('fm','CLP','YTD',None,None,0)
    assert calls==[('fm','CLP',date(2026,1,1),date(2026,9,24),None)]
    assert [v['net'] for v in r['rows']]==[40,0,None]
    assert r['methodology']=='reported_external'


@pytest.mark.parametrize('kind',['fm','fi'])
def test_long_windows_read_daily_facts_and_live_classifications(monkeypatch,kind):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,p:calls.append((sql,p)) or [])
    start,end=date(2021,9,25),date(2026,9,24)
    assert m.flow_data(kind,'USD',start,end)==[]
    assert len(calls)==1
    sql,p=calls[0]
    assert f'mv_nnm_daily_{kind}' in sql and f'categoria_{kind}_effective' in sql
    assert 'cartola_diaria' not in sql and 'valores_cuota_fi' not in sql
    assert p['start']==start and p['end']==end and p['currency']=='USD'
    if kind=='fm':
        assert 'fm_flow_adjustments' in sql and 'o.reported_observations>0' in sql
    else:
        assert 'previous_snapshot<:lookback' in sql


@pytest.mark.parametrize('params',[
    {'from_date':'2026-01-01'},
    {'from_date':'2026-01-02','to_date':'2026-01-01'},
    {'to_date':'2099-01-01'}, {'period':'bad'}, {'kind':'unknown'}, {'currency':'bad'},
])
def test_invalid_nnm_requests_fail_before_query(params):
    app=FastAPI();app.include_router(m.router)
    assert TestClient(app).get('/rankings/nnm',params=params).status_code==422


def test_mixed_returns_keep_kind_series_and_distinct_cutoff_dates(monkeypatch):
    monkeypatch.setattr(m,'classification_cache_epoch',lambda:0)
    def load(kind,currency,bucket):
        return dict(kind=kind,date=date(2026,9,24 if kind=='fm' else 23),
                    excluded_suspicious=1 if kind=='fm' else 0,
                    rows=[dict(kind=kind,currency=currency,run='1',serie='A')])
    monkeypatch.setattr(m,'load_returns',load)
    result=m.returns('all','USD')
    assert result['date'] is None
    assert result['dates']==dict(fm=date(2026,9,24),fi=date(2026,9,23))
    assert [r['kind'] for r in result['rows']]==['fm','fi']
    assert result['excluded_suspicious']==1


@pytest.mark.parametrize('custom',[False,True])
def test_mixed_nnm_uses_one_window_and_preserves_method_per_row(monkeypatch,custom):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda *args:[dict(date=date(2026,9,24)),dict(date=date(2026,9,20))])
    def load(kind,currency,period,start,end,bucket):
        calls.append((kind,currency,start,end))
        return dict(last_date=end,methodology='reported_external' if kind=='fm' else 'nav_implied',
                    rows=[dict(kind=kind,run='1',currency=currency,net=None if kind=='fi' else 0)])
    monkeypatch.setattr(m,'load_nnm',load);m.load_mixed_nnm.cache_clear()
    start,end=(date(2026,8,1),date(2026,8,31)) if custom else (None,None)
    result=m.load_mixed_nnm('CLP','YTD',start,end,0)
    expected_start,expected_end=(start,end) if custom else (date(2026,1,1),date(2026,9,20))
    assert calls==[(kind,'CLP',expected_start,expected_end) for kind in ('fm','fi')]
    assert [r['net'] for r in result['rows']]==[0,None]
    assert [r['methodology'] for r in result['rows']]==['reported_external','nav_implied']


def test_mixed_nnm_empty_currency_does_not_invent_window(monkeypatch):
    monkeypatch.setattr(api,'rows',lambda *args:[dict(date=None),dict(date=None)])
    m.load_mixed_nnm.cache_clear()
    assert m.load_mixed_nnm('PEN','1M',None,None,0)['rows']==[]


def test_nnm_matrix_scans_each_kind_once_and_keeps_each_periods_adjustments(monkeypatch):
    calls=[]
    def rows(sql,params=None):
        if 'MAX(latest_data_date)' in sql:
            return [dict(date=date(2026,9,24)),dict(date=date(2026,9,20))]
        return [dict(run='1',admin='AGF',category_type='Debt',category_group='Local')]
    def data(kind,currency,start,end,periods):
        calls.append((kind,currency,start,end,periods))
        return [dict(period=p,run='1',name='Fund',category='Debt',reported=v,migrations=a,
            observations=4,reported_observations=0 if v is None else 3,
            first_date=end,last_date=end,incomparable_nav=1)
            for p,v,a in [('1M',50,10),('1D',0,0),('YTD',None,None)]]
    monkeypatch.setattr(api,'rows',rows);monkeypatch.setattr(m,'flow_data',data)
    m.load_nnm_periods.cache_clear()
    result=m.load_nnm_periods('all','CLP',None,None,0)
    assert len(calls)==2 and [c[0] for c in calls]==['fm','fi']
    assert all(c[2]==date(2021,9,21) and c[3]==date(2026,9,20) for c in calls)
    assert all(len(c[4])==8 for c in calls)
    assert result['rows'][0]['values']['1M']['net']==40
    assert result['rows'][0]['values']['1D']['net']==0
    assert result['rows'][0]['values']['YTD']['net'] is None
    assert '5A' not in result['rows'][0]['values']  # Missing remains missing.
    assert result['rows'][1]['methodology']=='nav_implied'
    result=m.load_nnm_periods('all','CLP',date(2026,8,1),date(2026,8,31),1)
    assert result['periods'][-1]==dict(key='custom',start=date(2026,8,1),end=date(2026,8,31),truncated=False)
    assert all(c[3]==date(2026,8,31) for c in calls[-2:])


def test_nnm_matrix_empty_currency_and_bad_range(monkeypatch):
    monkeypatch.setattr(api,'rows',lambda *args:[dict(date=None)])
    m.load_nnm_periods.cache_clear()
    assert m.load_nnm_periods('all','PEN',None,None,0)['rows']==[]
    app=FastAPI();app.include_router(m.router)
    for params in [{'from_date':'2026-01-01'}, {'from_date':'2026-08-02','to_date':'2026-08-01'}, {'kind':'x'}]:
        assert TestClient(app).get('/rankings/nnm-periods',params=params).status_code==422


@pytest.mark.parametrize('window,expected', [('recent',{'1D','1W','1M','3M','6M','1A','YTD'}),('long',{'5A'})])
def test_nnm_progressive_windows_use_identical_explicit_cutoff(monkeypatch,window,expected):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda *args:[])
    monkeypatch.setattr(m,'flow_data',lambda kind,currency,start,end,periods:calls.append((start,end,periods)) or [])
    m.load_nnm_periods.cache_clear()
    result=m.load_nnm_periods('all','CLP',None,date(2026,9,24),0,window)
    assert {p['key'] for p in result['periods']}==expected
    assert len(calls)==2 and all(c[1]==date(2026,9,24) for c in calls)
    assert all({p for p,_ in c[2]}==expected for c in calls)
