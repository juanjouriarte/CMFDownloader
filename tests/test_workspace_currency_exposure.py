from datetime import date
from importlib import import_module
import pytest

m=import_module('src.api.industry_workspace.currency_exposure')


def record(code, weight, invalid=0, run='a', kind='fi', period=date(2026,6,30)):
    return dict(code=code,weight=weight,invalid=invalid,run=run,kind=kind,period=period,source='nac',positions=1)


def test_cmf_codes_are_not_assumed_to_be_iso():
    assert m.normalize(' UF ')=='UF'
    assert m.normalize('CLF')=='UF'
    assert m.normalize('$$')=='CLP'
    assert m.normalize('PROM')=='USD'
    assert m.normalize('USD')=='USD_SEGURO'
    assert m.normalize('CU')=='UNKNOWN'
    assert m.normalize('IPSA')=='UNKNOWN'
    assert m.normalize(None)=='UNKNOWN'
    assert m.normalize('JPY')=='JPY'


def test_partial_coverage_is_not_rescaled_and_unknown_is_preserved():
    s=m.summarize([record('UF',40),record('$$',20),record('CU',10)])
    assert s['uf']==40 and s['reported_weight']==70
    assert s['known_weight']==60 and s['unknown_weight']==10 and s['unreported_weight']==30
    assert s['valid']
    assert m.summarize([]) is None


@pytest.mark.parametrize('records',[
    [record('UF',None,1)], [record('UF',20),record('$$',None,1)],
    [record('UF',60),record('$$',50)], [record('UF',0)],
])
def test_incomparable_weights_never_become_zero_uf(records):
    result=m.summarize(records)
    assert not result['valid'] and result['uf'] is None


def fund(aum,exposure):
    return dict(aum=aum,exposure=exposure,nav_date=date(2026,6,30))


def test_weighted_sample_uses_nav_and_excludes_missing_and_invalid_reports():
    funds=[fund(100,m.summarize([record('UF',80)])),fund(300,m.summarize([record('$$',100)])),
           fund(200,None),fund(400,m.summarize([record('UF',None,1)]))]
    s=m.weighted_sample(funds)
    assert s['uf']==20 and s['aum_coverage']==40
    assert s['eligible_funds']==4 and s['reported_funds']==3 and s['sample_funds']==2 and s['excluded_funds']==1
    assert s['reported_weight']==95
    assert m.weighted_sample([fund(100,None)])['uf'] is None


def test_month_and_quarter_alignment():
    assert m.end_month(date(2026,6,1))==date(2026,6,30)
    assert m.shift(date(2026,3,31),-3)==date(2025,12,31)
    assert m.shift(date(2024,3,31),-1)==date(2024,2,29)


def test_history_does_not_carry_positions_forward():
    groups=m.group_positions([record('UF',50,period=date(2026,3,31))])
    assert ('fi','a',date(2026,6,30)) not in groups


def test_industry_uses_matching_dates_currency_and_historical_classification(monkeypatch):
    captured=[]
    monkeypatch.setattr(m,'read',lambda sql,params:captured.append((sql,params)) or [])
    m.nav_universe('fi','USD',date(2026,3,31),date(2026,6,30),None)
    sql,params=captured[0]
    assert 'm.currency=:currency' in sql and params['currency']=='USD'
    assert "c.periodo <= (m.month+interval '1 month - 1 day')::date" in sql
    assert 'm.month>=:start AND m.month<=:end' in sql
    assert 'f.vigente IS TRUE' in sql


def test_missing_previous_period_has_no_delta(monkeypatch):
    current=date(2026,6,30)
    monkeypatch.setattr(m,'period_choices',lambda kind:[current])
    monkeypatch.setattr(m,'reported_positions',lambda *args:[record('UF',70,period=current)])
    monkeypatch.setattr(m,'nav_universe',lambda *args:[dict(kind='fi',run='a',name='Fund',admin='AGF',period=current,aum=100,nav_date=current,category='Deuda',category_type='Deuda',category_code='debt')])
    result=m.industry_exposure(currency='CLP',kind='fi')
    assert result['funds'][0]['uf_change'] is None
    assert result['history'][-2]['uf'] is None
    assert result['history'][-1]['uf']==70
