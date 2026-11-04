from datetime import date
from decimal import Decimal
from importlib import import_module

import pytest
from src.fx import Rates
from src.api.industry_workspace.administrator_fx import monthly_samples

m = import_module('src.api.industry_workspace.administrator_fx')
api = import_module('src.api.industry_workspace.router')


def row(run, currency, aum, day=date(2026, 6, 30), admin='A', kind='fi'):
    return dict(run_fondo=run, currency=currency, aum=Decimal(str(aum)),
        month=day.replace(day=1), latest_data_date=day, admin=admin,
        kind=kind, category='Deuda', fund_name=run)


def rates(*points):
    return Rates([dict(currency=c, date=d, clp_per_unit=Decimal(str(v))) for c, d, v in points])


def test_multicurrency_fund_converts_then_deduplicates_and_preserves_native_drilldown():
    book = rates(('USD',date(2026,6,29),900), ('EUR',date(2026,6,30),1000))
    data = [row('1','USD',10),row('1','CLP',100),row('2','EUR',20)]
    converted, meta = monthly_samples(data,book)
    assert sum(r['aum'] for r in converted)==29100
    assert len(converted)==2 and converted[0]['native_currency'] is None
    assert converted[1]['native_currency']=='EUR'
    assert meta[date(2026,6,1)]['reported_funds']==2
    assert data[0]['aum']==10  # Never mutate native amounts.
    assert next(f for f in meta[date(2026,6,1)]['fx'] if f['currency']=='USD')['rate_date']==date(2026,6,29)


def test_currency_change_discards_old_currency_before_choosing_monthly_date():
    data = [row('1','CLP',9000,date(2026,6,29)),row('1','USD',10),row('2','CLP',500)]
    converted, meta = monthly_samples(data,rates(('USD',date(2026,6,30),900)))
    assert sum(r['aum'] for r in converted)==9500
    assert meta[date(2026,6,1)]['eligible_funds']==2
    assert converted[0]['native_currency']=='USD'


@pytest.mark.parametrize('fx_day',[date(2026,6,21),date(2026,7,1)])
def test_stale_or_future_fx_suppresses_snapshot_without_moving_common_date(fx_day):
    data = [row('1','USD',10),row('2','CLP',500),row('3','CLP',100,date(2026,6,29))]
    converted, meta = monthly_samples(data,rates(('USD',fx_day,900)))
    assert converted==[]
    assert meta[date(2026,6,1)]['date']==date(2026,6,30)
    assert meta[date(2026,6,1)]['missing_fx_funds']==1
    assert meta[date(2026,6,1)]['coverage_pct']==pytest.approx(200/3)


def test_analysis_growth_uses_each_month_fx_and_native_analysis_is_untouched(monkeypatch):
    data = [row('1','USD',10),row('2','CLP',1000,admin='B'),
            row('1','USD',10,date(2026,5,31)),row('2','CLP',1000,date(2026,5,31),admin='B')]
    book=rates(('USD',date(2026,5,31),800),('USD',date(2026,6,30),1000))
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:data)
    monkeypatch.setattr(api,'rows',lambda *a,**k:[{'month':date(2026,6,1)}])
    monkeypatch.setattr(m.Rates,'load',lambda *a:book)
    result=m.load_analysis.__wrapped__('all',date(2026,6,1),None,0)
    assert result['market_aum']==11000
    own=result['administrators'][0]
    assert own['aum']==10000 and own['mom']==25
    assert own['share']==pytest.approx(10000/11000*100)
    assert result['funds'][0]['change']==2000
    assert result['funds'][0]['native_currency']=='USD'
    assert result['mode']=='consolidated' and result['currency']=='CLP'


def test_missing_prior_fx_breaks_growth_instead_of_zero_or_older_base(monkeypatch):
    data=[row('1','USD',10),row('1','USD',10,date(2026,5,31))]
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:data)
    monkeypatch.setattr(api,'rows',lambda *a,**k:[{'month':date(2026,6,1)}])
    monkeypatch.setattr(m.Rates,'load',lambda *a:rates(('USD',date(2026,6,30),1000)))
    result=m.load_analysis.__wrapped__('fi',date(2026,6,1),None,0)
    assert result['administrators'][0]['mom'] is None
    assert result['funds'][0]['change'] is None
    assert result['history'][-2]['market_aum'] is None
    assert result['history'][-2]['missing_fx_funds']==1


def test_daily_history_uses_bounded_backward_fx_and_missing_market_suppresses_share(monkeypatch):
    calls=[]
    def query(sql, params):
        calls.append((sql,params))
        return [dict(date=date(2026,6,30),aum=Decimal(100),market_aum=None,
            funds=1,market_funds=2,missing_fx_funds=1)]
    monkeypatch.setattr(api,'rows',query)
    result=m.load_history.__wrapped__('all','AGF',date(2026,6,1),date(2026,6,30),None,'share',0)
    assert result['points'][0]['share'] is None
    sql,p=calls[0]
    assert 'BETWEEN p.date-:max_age AND p.date' in sql
    assert p['fx_start']==date(2026,5,25) and p['max_age']==7
    assert 'COUNT(DISTINCT(kind,run_fondo))' in sql
    assert 'f.vigente IS TRUE' in sql and 'f.fecha_termino_operaciones IS NULL' in sql


def test_missing_current_fx_does_not_claim_funds_stopped_reporting(monkeypatch):
    data=[row('1','USD',10),row('1','USD',10,date(2026,5,31))]
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:data)
    monkeypatch.setattr(api,'rows',lambda *a,**k:[{'month':date(2026,6,1)}])
    monkeypatch.setattr(m.Rates,'load',lambda *a:rates(('USD',date(2026,5,31),1000)))
    result=m.load_analysis.__wrapped__('fi',date(2026,6,1),None,0)
    assert result['market_aum'] is None and result['administrators']==[]
    assert result['funds']==[] and result['reported_funds']==1
    assert result['missing_fx_funds']==1 and result['date']==date(2026,6,30)


def test_long_history_uses_exact_date_boundary_and_keeps_missing_fx_gap(monkeypatch):
    original_history = m.load_history.__wrapped__
    data=[row('1','USD',10,date(2024,6,30)),row('1','USD',10,date(2025,5,31)),
          row('1','USD',10,date(2025,6,30))]
    book=rates(('USD',date(2025,6,30),1000))
    monkeypatch.setattr(api,'monthly_data',lambda *a,**k:data)
    monkeypatch.setattr(m.Rates,'load',lambda *a:book)
    boundary=[]
    def daily(kind,admin,start,end,category,metric,bucket):
        boundary.append((start,end))
        return dict(points=[dict(date=end,aum=10000,share=100,funds=1,market_funds=1,missing_fx_funds=0)])
    monkeypatch.setattr(m,'load_history',daily)
    # Keep reference to the uncached implementation before replacing the recursive call.
    result=original_history('fi','A',date(2024,7,1),date(2026,6,15),None,'share',0)
    assert boundary==[(date(2026,6,1),date(2026,6,15))]
    assert [p['date'] for p in result['points']]==[date(2025,5,31),date(2025,6,30),date(2026,6,15)]
    assert result['points'][0]['aum'] is None and result['points'][0]['missing_fx_funds']==1
    assert result['points'][1]['aum']==10000
