from datetime import date
import pytest
from src.api.industry_workspace import ownership_analysis as module


def row(rut='111',run='one',admin='BTG Pactual AGF',weight=10,**kw):
    return dict(rut=rut,run=run,admin=admin,weight=weight,name='Owner '+rut,rank=1,
                person_type='J',fund_name='Fund '+run,category='Debt',**kw)


def nav(aum=1000,currency='CLP'):
    return dict(aum=aum,currency=currency,date=date(2026,6,30))


def test_rut_identity_rejects_placeholders_and_preserves_exact_keys():
    assert module.rut_key('0011.111.111-1')=='11111111'
    assert module.rut_key('11.111.111-K')=='11111111'
    assert module.rut_key('0') is None
    assert module.rut_key('foreign-123') is None
    assert module.rut_key('11111112')!='11111111'


def test_comparison_union_shared_and_missing_values_are_not_zero():
    rows=[row(),row(run='two',admin='LV'),row('222',run='two',admin='LV'),row('333',run='usd',admin='LV'),row('444',run='missing',admin='LV'),row('555',weight=0),row('666',admin='Third')]
    items=module.compare_rows(rows,{'one':nav(),'two':nav(2000),'usd':nav(3000,'USD')},'CLP','BTG Pactual AGF','LV')
    by={r['rut']:r for r in items}
    assert len(items)==5
    assert by['111']['side']=='shared' and by['111']['combined']['amount']==300
    assert by['222']['side']=='b' and by['222']['a']['amount'] is None
    assert by['333']['combined']['amount'] is None and by['333']['combined']['other_currency']==1
    assert by['444']['combined']['missing']==1
    assert by['555']['combined']['amount']==0
    assert sum(r['combined']['amount'] or 0 for r in items)==500


def test_amount_change_bridge_reconciles_without_claiming_flows():
    bridge=module.movement_effect(row(weight=10),row(weight=20),nav(1000),nav(1500),'CLP')
    assert bridge==dict(amount_change=200,patrimonio_effect=50,ownership_effect=150)
    for old,new,n0,n1 in [(None,row(),nav(),nav()),(row(weight=None),row(),nav(),nav()),(row(),row(),nav(currency='USD'),nav())]:
        assert module.movement_effect(old,new,n0,n1,'CLP')['amount_change'] is None


def test_btg_fraction_uses_observed_valuations_only():
    rows=module.amount_rows([row(),row(run='two',admin='LV'),row(run='missing',admin='LV')],{'one':nav(),'two':nav(3000)},'CLP')
    result=module.btg_share(rows,'CLP')
    assert result['share']==25 and result['total']['missing']==1
    assert module.btg_share([], 'CLP')['share'] is None
    assert module.btg_share([], 'CLP')['btg']['amount'] is None
    foreign=module.amount_rows([row(run='usd'),row(admin='LV')],{'usd':nav(currency='USD'),'one':nav()},'CLP')
    assert module.btg_share(foreign,'CLP')['share']==0


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    latest,previous=date(2026,6,30),date(2026,3,31)
    now=[row(),row(run='two',admin='LV',weight=20),row('222',run='two',admin='LV'),row('333',run='missing',admin='LV')]
    prior=[row(weight=15),row('old',run='two',admin='LV')]
    monkeypatch.setattr(module.base,'selected_period',lambda p:(p or latest,[latest,previous],0))
    monkeypatch.setattr(module,'source',lambda p,b:now if p==latest else prior if p==previous else [])
    monkeypatch.setattr(module.base,'nav_snapshot',lambda p,b,age:{'one':nav(),'two':nav(2000)} if p==latest else {'one':nav(800)})
    app=FastAPI();app.include_router(module.router)
    with TestClient(app) as c:yield c


def test_api_comparison_same_period_union_currency_and_invalid_options(client):
    response=client.get('/ownership-comparison',params={'admin_a':'BTG Pactual AGF','admin_b':'LV'})
    assert response.status_code==200,response.text
    d=response.json();assert d['summary']['unique']==3 and d['summary']['shared']==1
    assert d['summary']['only_b']==2 and d['summary']['only_a']==0
    assert d['summary']['combined']['amount']==700
    assert d['summary']['combined']['missing']==1
    assert client.get('/ownership-comparison?admin_a=LV&admin_b=LV').status_code==422
    assert client.get('/ownership-comparison?admin_a=Missing&admin_b=LV').status_code==422
    assert client.get('/ownership-comparison?nav_days=8').status_code==422
    usd=client.get('/ownership-comparison',params={'admin_a':'BTG Pactual AGF','admin_b':'LV','currency':'USD'}).json()
    assert usd['summary']['unique']==3 and usd['summary']['combined']['amount'] is None


def test_radar_filter_sort_and_missing_reports(client):
    d=client.get('/ownership-analysis?sort=delta&direction=asc&limit=1').json()
    assert d['rows'][0]['rut']=='111' and d['rows'][0]['delta']==-5
    assert d['movements']['unlisted']==1
    assert client.get('/ownership-analysis?offset=100').json()['rows']==[]
    assert client.get('/ownership-analysis?admin=LV&category=Debt').json()['summary']['owners']==3
    assert client.get('/ownership-analysis?category=Equity').json()['total']==0
    assert client.get('/ownership-analysis?run=two').json()['summary']['funds']==1


def test_profile_history_has_no_carry_forward_and_bridge_scope(client):
    r=client.get('/ownership-analysis/111');assert r.status_code==200,r.text
    d=r.json();assert len(d['history'])==8
    assert d['history'][0]['total']['amount'] is None
    assert d['total']['amount']==500
    two=next(r for r in d['amounts'] if r['run']=='two')
    assert two['amounts'][-2] is None and two['amounts'][-1]==400
    assert next(r for r in d['bridge'] if r['run']=='two')['amount_change'] is None


def test_overallocated_fund_keeps_reported_weights_but_cannot_be_valued():
    rows=module.base.positions([row('111',weight=60),row('222',weight=60)])
    assert rows[0]['weight']==60 and rows[0]['invalid_total']
    assert module.base.value_positions(rows,{'one':nav()},'CLP')['aum'] is None
    assert module.amount_rows(rows,{'one':nav()},'CLP')[0]['amount'] is None
    assert module.movement_effect(rows[0],rows[0],nav(),nav(),'CLP')['amount_change'] is None
