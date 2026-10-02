from datetime import date
from decimal import Decimal
from importlib import import_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

m=import_module('src.api.industry_workspace.administrator_timeseries')
api=import_module('src.api.industry_workspace.router')


def flow(run,category,aportes,rescates,migrations=0):
    known=aportes is not None and rescates is not None
    return dict(run=run,name=run,category=category,aportes=aportes,rescates=rescates,
        reported=aportes-rescates if known else None,migrations=migrations if known else None,
        observations=4,reported_observations=4 if known else 0,
        first_date=date(2026,1,1),last_date=date(2026,1,4))


def test_tree_totals_reconcile_to_children_and_keep_unknowns():
    d=m.flow_tree([flow('a','Deuda',Decimal('100'),Decimal('20'),Decimal('10')),
        flow('b','Deuda',10,40),flow('c','Acciones',None,None),flow('d','Mixta',0,0)])
    assert d['total']['net']==40 and d['total']['reported']==50
    assert d['total']['funds']==4 and d['total']['reported_funds']==3
    assert d['total']['observations']==16 and d['total']['reported_observations']==12
    unknown=next(c for c in d['categories'] if c['name']=='Acciones')
    assert unknown['net'] is None and unknown['children'][0]['net'] is None
    assert next(c for c in d['categories'] if c['name']=='Mixta')['net']==0
    for c in d['categories']:
        if c['net'] is not None:assert c['net']==sum(f['net'] for f in c['children'] if f['net'] is not None)


def test_daily_history_and_share_use_same_dates_and_bound_filters(monkeypatch):
    calls=[]
    def rows(sql,params):
        calls.append((sql,params))
        return [dict(date=date(2026,1,2),aum=Decimal('25'),funds=2,market_aum=Decimal('100'),market_funds=8),
                dict(date=date(2026,1,3),aum=None,funds=0,market_aum=Decimal('100'),market_funds=7)]
    monkeypatch.setattr(api,'rows',rows);m.load_history.cache_clear()
    result=m.load_history('PEN','fi','AGF',date(2026,1,1),date(2026,1,7),'Deuda','share',0)
    assert result['granularity']=='daily' and result['points'][0]['share']==25
    assert result['points'][1]['share'] is None and result['points'][1]['aum'] is None
    sql,p=calls[0]
    assert p['currency']=='PEN' and p['admin']=='AGF' and p['category']=='Deuda'
    assert 'valores_cuota_fi' in sql and 'cartola_diaria' not in sql and 'f.vigente IS TRUE' in sql
    m.load_history('USD','fm','AGF',date(2026,1,1),date(2026,1,7),None,'aum',0)
    assert "COALESCE(f.razon_social_administradora, 'Sin administradora')=:admin" in calls[-1][0]


def test_long_history_uses_materialized_common_dates_and_exact_final_month(monkeypatch):
    monthly=[dict(month=date(2025,1,1),latest_data_date=date(2025,1,29),aum=100,
                  admin='A',run_fondo='1',kind='fm',category='Deuda'),
             dict(month=date(2025,2,1),latest_data_date=date(2025,2,28),aum=120,
                  admin='A',run_fondo='1',kind='fm',category='Deuda')]
    calls=[]
    def daily(sql,p):
        calls.append(p)
        return [dict(date=date(2026,1,15),aum=150,funds=1,market_aum=300,market_funds=2)]
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:monthly)
    monkeypatch.setattr(api,'rows',daily);m.load_history.cache_clear()
    result=m.load_history('CLP','all','A',date(2024,1,1),date(2026,1,15),None,'share',0)
    assert result['granularity']=='monthly'
    assert [p['date'] for p in result['points']]==[date(2025,1,29),date(2025,2,28),date(2026,1,15)]
    assert result['points'][-1]['share']==50
    # Only the final partial month scans daily data, not the whole multi-year range.
    assert calls[0]['start']==date(2026,1,1) and calls[0]['end']==date(2026,1,15)


def test_monthly_history_does_not_include_nav_before_custom_start(monkeypatch):
    monthly=[dict(month=date(2024,1,1),latest_data_date=date(2024,1,10),aum=100,
                  admin='A',run_fondo='1',kind='fm',category='Deuda')]
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:monthly)
    monkeypatch.setattr(api,'rows',lambda *a:[]);m.load_history.cache_clear()
    result=m.load_history('CLP','fm','A',date(2024,1,20),date(2026,1,15),None,'aum',0)
    assert result['points']==[]


def test_flows_query_scopes_migrations_to_currency_fund_and_observed_day(monkeypatch):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,p:calls.append((sql,p)) or [])
    m.load_flows.cache_clear()
    d=m.load_flows('USD','A',date(2026,1,1),date(2026,1,31),'Deuda',0)
    sql,p=calls[0]
    assert p['admin']=='A' and p['currency']=='USD'
    assert "a.status IN ('auto_confirmed','confirmed')" in sql
    assert 'a.currency=:currency' in sql and 'o.fecha=a.event_date' in sql
    assert 'o.run_fondo=a.target_run_fondo' in sql
    assert 'COUNT(aportes)' in sql and 'cd.monto_rescatado>=0' in sql
    assert d['total']['net'] is None and d['last_date'] is None


@pytest.mark.parametrize('endpoint',['history','flows'])
@pytest.mark.parametrize('start,end',[('2019-12-31','2026-01-01'),('2026-01-02','2026-01-01'),('2026-01-01','2099-01-01')])
def test_invalid_ranges_return_422(endpoint,start,end):
    app=FastAPI();app.include_router(m.router)
    response=TestClient(app).get('/administrators/'+endpoint,params=dict(admin='A',from_date=start,to_date=end))
    assert response.status_code==422


def test_flow_windows_share_one_end_and_use_correct_inclusive_boundaries():
    windows={p['key']:p for p in m.flow_windows(date(2026,9,24))}
    assert list(windows)==['1D','1W','1M','3M','6M','1A','5A','YTD']
    assert windows['1D']['start']==date(2026,9,24)
    assert windows['1W']['start']==date(2026,9,18)
    assert windows['1M']['start']==date(2026,8,25)
    assert windows['3M']['start']==date(2026,6,25)
    assert windows['6M']['start']==date(2026,3,25)
    assert windows['1A']['start']==date(2025,9,25)
    assert windows['5A']['start']==date(2021,9,25)
    assert windows['YTD']['start']==date(2026,1,1)
    assert all(p['end']==date(2026,9,24) for p in windows.values())
    leap={p['key']:p for p in m.flow_windows(date(2024,3,31))}
    assert leap['1M']['start']==date(2024,3,1)
    assert leap['5A']['truncated'] and leap['5A']['start']==date(2020,1,1)


def test_flow_matrix_aligns_funds_without_filling_missing_periods_with_zero():
    periods=m.flow_windows(date(2026,9,24))
    rows=[dict(**flow('a','Deuda',100,20,10),period='1D'),
          dict(**flow('b','Deuda',None,None),period='1D'),
          dict(**flow('a','Deuda',200,30,10),period='YTD'),
          dict(**flow('b','Deuda',0,0),period='YTD'),
          dict(**flow('c','Acciones',10,30),period='YTD')]
    d=m.build_flow_matrix(rows,periods)
    assert d['total']['funds']==3
    assert d['total']['values']['1D']['net']==70
    assert d['total']['values']['YTD']['net']==140
    assert d['total']['values']['5A']['net'] is None
    deuda=next(c for c in d['categories'] if c['name']=='Deuda')
    b=next(f for f in deuda['children'] if f['run']=='b')
    assert b['values']['1D']['net'] is None and b['values']['YTD']['net']==0
    assert '1W' not in b['values']
    for period in periods:
        key=period['key']
        cells=[c['values'][key]['net'] for c in d['categories'] if key in c['values'] and c['values'][key]['net'] is not None]
        assert d['total']['values'][key]['net']==(sum(cells) if cells else None)


def test_flow_matrix_uses_one_bound_query_for_all_periods(monkeypatch):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,p:calls.append((sql,p)) or [])
    m.load_flow_matrix.cache_clear()
    d=m.load_flow_matrix('USD','A',date(2026,9,24),None,0)
    assert len(calls)==1 and len(d['periods'])==8
    sql,p=calls[0]
    assert p['currency']=='USD' and p['start']==date(2021,9,25)
    assert p['start_0']==p['end'] and p['period_7']=='YTD'
    assert 'USING(period,run_fondo)' in sql and 'o.fecha>=w.start_date' in sql
