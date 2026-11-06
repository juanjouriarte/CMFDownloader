from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest
from src.api.category_analytics import category_key, summarize, present, router
from src.api.industry_workspace.administrator_equity import mixed_samples
from src.fx import Rates


def nav(run, value, admin='BTG Pactual', kind='fm', month=date(2026,9,1), currency='CLP'):
    return dict(kind=kind,run_fondo=run,fund_name=run,admin=admin,category='Deuda',
        month=month,latest_data_date=month.replace(day=30),aum=Decimal(value),currency=currency)


def build(raw, reports=None, candidates=None, rates=None, currency='CLP'):
    cutoffs={date(2025,9,1):date(2025,9,30),date(2026,8,1):date(2026,8,31),date(2026,9,1):date(2026,9,30)}
    sample,coverage=mixed_samples(raw,candidates or [],reports or [],cutoffs,'all',currency,rates=rates)
    data=summarize(sample,coverage,{})
    return dict(data,latest=date(2026,9,30),from_date=date(2026,8,1),to_date=date(2026,9,30))


def test_category_and_admin_denominators_are_distinct_and_reconcile():
    raw=[nav('1','20'),nav('2','30','Other'),dict(nav('3','50'),category='Accionario')]
    data=build(raw);key=category_key(raw[0]);result=present(data,key)
    category=next(r for r in result['ranking'] if r['id']==key)
    assert category['share']==50 and category['btg_share']==40
    assert sum(a['share'] for a in result['administrators'])==100
    assert sum(f['share'] for f in result['funds'])==100
    assert result['history'][-1]['industry_aum']==100


def test_same_names_across_fm_fi_and_paths_stay_separate():
    a=nav('1','20');b=nav('1','30',kind='fi')
    assert category_key(a)!=category_key(b)
    assert category_key(dict(a,category_group='A'))!=category_key(dict(a,category_group='B'))
    assert len(present(build([a,b]))['ranking'])==2


def test_missing_month_never_becomes_zero_or_false_admin_exit():
    a=nav('1','20');result=present(build([a]),category_key(a))
    assert result['history'][0]['aum'] is None
    assert result['ranking'][0]['mom'] is None
    assert result['administrators'][0]['share_change'] is None


def test_mom_yoy_and_share_changes_use_matching_calendar_months():
    a=nav('1','30')
    data=build([a,nav('2','30','Other'),nav('1','20',month=date(2026,8,1)),nav('2','20','Other',month=date(2026,8,1)),nav('1','15',month=date(2025,9,1))])
    result=present(data,category_key(a));c=result['ranking'][0]
    assert c['mom']==50 and c['yoy']==300
    assert result['administrators'][0]['share_change']==0
    data['from_date']=date(2026,9,1)
    assert present(data,category_key(a))['history'][0]['admins'][0]['share_change']==0


def test_zero_aum_retained_without_division_by_zero():
    a=nav('1','0');r=present(build([a]),category_key(a))
    assert r['ranking'][0]['aum']==0 and r['ranking'][0]['share'] is None
    assert r['administrators'][0]['share'] is None


def test_missing_fx_suppresses_full_market_and_shares():
    a=nav('1','20',currency='USD')
    r=present(build([a],rates=Rates([]),currency=None),category_key(a))
    assert r['coverage']['missing_fx_funds']==1
    assert r['coverage']['total'] is None and not r['ranking']
    assert r['history'][-1]['share'] is None


def test_eeff_conversion_uses_actual_report_date_not_display_month():
    c=dict(kind='fi',run_fondo='q',name='Quarterly',admin='Other',category='Deuda')
    report=dict(run_fondo='q',periodo=date(2026,6,30),equity=Decimal(10),currency='USD',source_url='https://cmfchile.cl')
    rates=Rates([dict(currency='USD',date=date(2026,6,30),clp_per_unit=Decimal(900)),dict(currency='USD',date=date(2026,9,30),clp_per_unit=Decimal(1000))])
    r=present(build([],reports=[report],candidates=[c],rates=rates,currency=None),category_key(c))
    assert r['ranking'][0]['aum']==9000
    assert r['ranking'][0]['eeff_funds']==1 and r['funds'][0]['date']==date(2026,6,30)
    assert r['ranking'][0]['mom']==0


@pytest.mark.parametrize('query',['kind=invalid','currency=bad','source=bad','from_date=2019-01-01','from_date=2026-09-01&to_date=2026-08-01','to_date=2099-01-01','to_date=2019-01-31'])
def test_invalid_requests_rejected_before_data_load(query):
    app=FastAPI();app.include_router(router)
    assert TestClient(app).get('/industry-workspace/category-analysis?'+query).status_code==422
