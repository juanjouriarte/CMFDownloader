from datetime import date
from decimal import Decimal
from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.fx import Rates
from src.api.industry_workspace.administrator_dates import comparison_dates, build_dated_analysis, daily_data

api=import_module('src.api.industry_workspace.router')


def row(run,day,amount,currency='CLP',admin='A'):
    return dict(run_fondo=run,kind='fi',fund_name=run,admin=admin,category='Deuda',
        month=day.replace(day=1),latest_data_date=day,aum=Decimal(amount),currency=currency)


def test_same_day_comparisons_clamp_leap_and_short_months():
    dates=comparison_dates(date(2024,3,31))
    assert dates[date(2024,2,1)]==date(2024,2,29)
    assert dates[date(2023,3,1)]==date(2023,3,31)
    assert comparison_dates(date(2024,2,29))[date(2023,2,1)]==date(2023,2,28)
    assert list(comparison_dates(date(2020,1,15)).values())==[date(2020,1,15)]


def test_month_end_uses_latest_day_not_most_covered_day():
    raw=[row('1',date(2026,9,29),100),row('2',date(2026,9,29),200),
         row('3',date(2026,9,30),300)]
    d=build_dated_analysis(raw,date(2026,9,1),'CLP','fi',None,'month_end',date(2026,9,30))
    assert d['date']==date(2026,9,30) and d['market_aum']==300
    assert d['reported_funds']==1 and d['eligible_funds']==3
    assert d['coverage_pct']==pytest.approx(100/3)


def test_exact_day_empty_does_not_substitute_previous_month_or_invent_expected_filings():
    raw=[row('1',date(2026,8,15),100)]
    d=build_dated_analysis(raw,date(2026,9,1),'CLP','fi',None,'day',date(2026,9,15))
    assert d['date'] is None and d['market_aum'] is None
    assert d['as_of']==date(2026,9,15) and d['eligible_funds'] is None and d['coverage_pct'] is None


def test_exact_day_fx_growth_uses_comparison_day_rates_and_preserves_original_currency():
    raw=[row('1',date(2026,9,15),10,'USD'),row('1',date(2026,8,15),10,'USD')]
    book=Rates([dict(currency='USD',date=date(2026,9,15),clp_per_unit=Decimal(900)),
                dict(currency='USD',date=date(2026,8,15),clp_per_unit=Decimal(800))])
    d=build_dated_analysis(raw,date(2026,9,1),'CLP','fi',None,'day',date(2026,9,15),book)
    assert d['market_aum']==9000 and d['funds'][0]['native_currency']=='USD'
    assert d['administrators'][0]['mom']==12.5 and d['funds'][0]['change']==1000
    assert d['previous_date']==date(2026,8,15)


def test_daily_source_restricts_to_exact_dates_currency_and_active_funds(monkeypatch):
    calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,p:calls.append((sql,p)) or [])
    daily_data('USD','all',comparison_dates(date(2026,9,15)))
    sql,p=calls[0]
    assert 'fecha=ANY(:dates)' in sql and 'f.vigente IS TRUE' in sql and 'f.fecha_termino_operaciones IS NULL' in sql
    assert p['currency']=='USD' and len(p['dates'])==13
    assert all(d.day==15 for d in p['dates'])


@pytest.mark.parametrize('query',[
    'date_mode=day', 'as_of=2019-12-31', 'as_of=2100-01-01',
    'as_of=2026-09-15&period=2026-09-01', 'as_of=2026-09-15&date_mode=month_end',
    'as_of=2026-09-15&date_mode=latest', 'period=2026-09-01&date_mode=latest',
])
def test_invalid_or_ambiguous_date_requests_rejected(query):
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get('/industry-workspace/administrators/analysis?'+query).status_code==422


def test_default_analysis_uses_panorama_latest_policy(monkeypatch):
    from src.api.industry_workspace import administrator_dates
    from src.api.industry_workspace import administrators
    calls=[]
    monkeypatch.setattr(administrators,'classification_cache_epoch',lambda:0)
    monkeypatch.setattr(administrator_dates,'load_analysis',lambda *args:calls.append(args) or {})
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get('/industry-workspace/administrators/analysis').status_code==200
        assert calls[-1][4]=='latest'
        assert client.get('/industry-workspace/administrators/analysis?as_of=2026-09-15').status_code==200
        assert calls[-1][4]=='day' and calls[-1][3]==date(2026,9,15)
