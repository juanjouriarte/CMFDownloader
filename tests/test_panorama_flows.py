from datetime import date
from decimal import Decimal
from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.panorama_flows import monthly_flows
from src.fx import Rates

api=import_module('src.api.panorama_flows')


def point(day,value='10',currency='USD',valid=2,total=2,migrations='0'):
    value=Decimal(value) if value is not None else None
    migration=Decimal(migrations)
    return dict(date=date.fromisoformat(day),currency=currency,amount=value,
        reported=value+migration if value is not None else None,migrations=migration,
        observations=total,valid_observations=valid)


def test_daily_fx_signed_flow_and_migration_adjustment():
    book=Rates([dict(currency='USD',date=date(2026,9,1),clp_per_unit=Decimal(900)),
                dict(currency='USD',date=date(2026,9,2),clp_per_unit=Decimal(1000))])
    d=monthly_flows([point('2026-09-01','10',migrations='2'),point('2026-09-02','-5')],date(2026,9,1),date(2026,9,30),book)[0]
    assert d['amount']==4000 and d['reported']==5800 and d['migrations']==1800
    assert d['valid_observations']==4 and not d['partial']


def test_native_currency_never_requires_fx_and_valid_zero_stays_zero():
    d=monthly_flows([point('2026-09-01','0')],date(2026,9,1),date(2026,9,30))[0]
    assert d['amount']==0 and d['state']=='available'


def test_missing_fx_suppresses_month_and_preserves_observed_subtotal():
    d=monthly_flows([point('2026-09-01','10','CLP'),point('2026-09-02','20','USD')],date(2026,9,1),date(2026,9,30),Rates([]))[0]
    assert d['amount'] is None and d['covered_amount']==10
    assert d['missing_fx_points']==1 and d['state']=='missing_fx'


def test_missing_month_is_not_zero_and_last_report_month_is_partial():
    data=monthly_flows([point('2026-10-27','1',valid=1,total=2)],date(2026,9,1),date(2026,10,27))
    assert data[0]['amount'] is None and data[0]['state']=='no_data'
    assert data[1]['partial'] and data[1]['incomplete_data'] and data[1]['state']=='partial'
    assert data[1]['last_date']==date(2026,10,27)


def test_invalid_observations_not_treated_as_missing_fx_or_zero():
    d=monthly_flows([point('2026-09-01',None,valid=0)],date(2026,9,1),date(2026,9,30),Rates([]))[0]
    assert d['amount'] is None and d['missing_fx_points']==0 and d['state']=='no_valid_data'


@pytest.mark.parametrize('params',[{'from_date':'2019-01-01'},{'from_date':'2026-09-01','to_date':'2026-08-01'},{'kind':'all'},{'currency':'$'}])
def test_invalid_ranges_and_currency_rejected(params):
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get('/industry/panorama/flows',params=params).status_code==422


def test_sql_uses_existing_daily_facts_and_live_category_filters(monkeypatch):
    calls=[]
    def rows(sql,p=None):
        calls.append((sql,p));return [{'cutoff':date(2026,9,30)}] if 'MAX(fecha)' in sql else []
    monkeypatch.setattr(api,'_rows',rows)
    api.load_flows.cache_clear()
    api.load_flows('fi','USD',date(2026,9,1),date(2026,9,30),'BTG','Debt','Deuda','Local',0)
    sql,args=calls[-1]
    assert 'v_nnm_daily_fi_continuous' in sql and 'previous_snapshot' in sql and 'exclusion IS NULL' in sql
    assert args['currency']=='USD' and args['category']=='Debt' and args['admin']=='%BTG%'
    assert 'f.vigente IS TRUE' in sql
    api.load_flows.cache_clear()
