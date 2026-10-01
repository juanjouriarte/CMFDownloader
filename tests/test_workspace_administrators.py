from datetime import date
from decimal import Decimal
from importlib import import_module
import pytest
m=import_module('src.api.industry_workspace.administrators')

def row(admin,run,aum,month=date(2026,6,1),day=30,kind='fi',category='Deuda'):
    return dict(admin=admin,run_fondo=run,aum=Decimal(str(aum)),month=month,
                latest_data_date=month.replace(day=day),kind=kind,category=category,fund_name='Fondo '+run)

def test_exact_month_bases_ranks_mix_and_native_amounts():
    data=[row('A','1',60),row('A','2',40,kind='fm',category='Acciones'),row('B','3',100),
          row('A','1',50,date(2026,5,1)),row('B','3',150,date(2026,5,1)),
          row('A','1',20,date(2025,6,1))]
    d=m.build_analysis(data,date(2026,6,1),'USD','all')
    a=d['administrators'][0]
    assert d['market_aum']==200 and a['share']==50 and a['mom']==100 and a['yoy']==400
    assert a['rank']==1 and a['rank_change']==1 and a['share_change']==25
    assert a['fm_aum']==40 and a['fi_aum']==60 and a['top5_pct']==100
    cat=next(c for c in a['categories'] if c['name']=='Deuda')
    assert cat['share']==60 and cat['market_share']==37.5
    assert len(d['history'])==13 and d['history'][1]['market_aum'] is None
    assert next(f for f in d['funds'] if f['run']=='2')['change'] is None
    assert next(f for f in d['funds'] if f['run']=='1')['change']==10


def test_missing_exact_previous_month_is_not_substituted():
    data=[row('A','1',100),row('A','1',50,date(2026,4,1))]
    d=m.build_analysis(data,date(2026,6,1),'CLP','fi')
    a=d['administrators'][0]
    assert a['mom'] is None and a['yoy'] is None and a['rank_change'] is None
    assert d['previous_date'] is None


def test_common_date_excludes_other_dates_and_discloses_coverage():
    data=[row('A','1',100),row('B','2',200),row('C','3',900,day=29)]
    d=m.build_analysis(data,date(2026,6,1),'CLP','fi')
    assert d['market_aum']==300 and d['reported_funds']==2 and d['eligible_funds']==3
    assert d['coverage_pct']==pytest.approx(200/3)
    assert sum(a['share'] for a in d['administrators'])==pytest.approx(100)


def test_category_denominators_and_missing_current_funds():
    data=[row('A','1',100),row('B','2',300),row('C','3',900,category='Acciones'),
          row('A','old',20,date(2026,5,1))]
    d=m.build_analysis(data,date(2026,6,1),'CLP','fi','Deuda')
    assert d['market_aum']==400 and len(d['administrators'])==2
    old=next(f for f in d['funds'] if f['run']=='old')
    assert old['aum'] is None and old['change'] is None and old['status']=='not_reported'
    assert next(a for a in d['administrators'] if a['admin']=='A')['categories'][0]['market_share']==25


def test_active_query_is_opt_in_and_currency_is_bound(monkeypatch):
    api=import_module('src.api.industry_workspace.router');calls=[]
    monkeypatch.setattr(api,'rows',lambda sql,params:calls.append((sql,params)) or [])
    api.monthly_data('PEN','all',date(2025,6,1),date(2026,6,1),active_only=True)
    sql,p=calls[0]
    assert 'f.vigente IS TRUE' in sql and 'f.fecha_termino_operaciones IS NULL' in sql
    assert p['currency']=='PEN' and 'm.currency=:currency' in sql
