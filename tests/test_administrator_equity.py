from datetime import date
from decimal import Decimal

import pytest

from src.api.industry_workspace.administrator_equity import analyze, mixed_samples, report_data
from src.fx import Rates

SEP=date(2026,9,1)
CUT=date(2026,9,30)
JUN=date(2026,6,30)


def candidate(run='q',admin='BTG',category='Deuda'):
    return dict(kind='fi',run_fondo=run,name=run,admin=admin,category=category)


def report(run='q',day=JUN,value='20',currency='USD'):
    return dict(run_fondo=run,periodo=day,equity=Decimal(value),currency=currency,source_url='https://cmf.cl/report')


def nav(run='n',day=CUT,value='100',kind='fi'):
    return dict(kind=kind,run_fondo=run,fund_name=run,admin='Other',category='Deuda',
        month=day.replace(day=1),latest_data_date=day,aum=Decimal(value),currency='CLP')


def test_expanded_ranking_uses_report_fx_date_and_retains_actual_dates():
    rates=Rates([dict(currency='USD',date=JUN,clp_per_unit=Decimal(900)),
                 dict(currency='USD',date=CUT,clp_per_unit=Decimal(1000))])
    d=analyze([nav()],[candidate()],[report()],{SEP:CUT},SEP,'CLP','all',None,'month_end',CUT,'all',rates)
    assert d['market_aum']==18100 and d['reported_funds']==2
    assert d['nav_funds']==1 and d['eeff_funds']==1
    f=next(f for f in d['funds'] if f['run']=='q')
    assert f['date']==JUN and f['aum']==18000 and f['aum_source']=='quarterly_report'
    assert f['native_currency']=='USD'
    assert sum(a['share'] for a in d['administrators'])==pytest.approx(100)
    assert next(a for a in d['administrators'] if a['admin']=='BTG')['fi_aum']==18000
    assert d['fx'][1]['nav_date']==JUN


def test_latest_report_selected_before_native_currency_and_invalid_value_filters():
    for newer in [report(day=CUT,currency='CLP'),report(day=CUT,value='-1')]:
        rows,coverage=mixed_samples([], [candidate()],[report(),newer],{SEP:CUT},'quarterly_report','USD')
        assert rows==[]  # Do not revive the older USD/positive report.


def test_future_stale_and_missing_reports_excluded_without_forward_fill():
    candidates=[candidate(r) for r in ('future','stale','missing')]
    reports=[report('future',date(2026,12,31)),report('stale',date(2025,12,31))]
    rows,coverage=mixed_samples([],candidates,reports,{SEP:CUT},'all',None)
    assert rows==[]
    assert {r['reason'] for r in coverage[SEP]['excluded_reports']}=={'missing_report','stale_report'}


def test_reports_never_duplicate_nav_and_report_only_drops_nav():
    raw=[nav('q')]
    candidates=[candidate()]
    rows,_=mixed_samples(raw,candidates,[report(currency='CLP')],{SEP:CUT},'all','CLP')
    assert len(rows)==1 and rows[0]['aum']==100 and rows[0]['aum_source']=='nav'
    rows,_=mixed_samples(raw,candidates,[report(currency='CLP')],{SEP:CUT},'quarterly_report','CLP')
    assert len(rows)==1 and rows[0]['aum']==20


def test_missing_report_fx_suppresses_complete_ranking_and_false_departures():
    d=analyze([nav()],[candidate()],[report()],{SEP:CUT},SEP,'CLP','all',None,'month_end',CUT,'all',Rates([]))
    assert d['market_aum'] is None and d['administrators']==[] and d['funds']==[]
    assert d['missing_fx_funds']==1 and d['reported_funds']==2


def test_category_filters_both_sources_and_zero_equity_is_valid():
    rows,meta=mixed_samples([nav()],[candidate(category='Inmobiliario')],[report(value='0',currency='CLP')],
        {SEP:CUT},'all','CLP','Inmobiliario')
    assert len(rows)==1 and rows[0]['aum']==0 and meta[SEP]['eeff_funds']==1


def test_same_report_across_months_is_zero_change_not_new_money():
    aug=date(2026,8,1)
    d=analyze([], [candidate()], [report(currency='CLP')],{aug:date(2026,8,31),SEP:CUT},SEP,
        'CLP','fi',None,'month_end',CUT,'quarterly_report')
    assert d['funds'][0]['change']==0
    assert d['funds'][0]['previous_date']==JUN and d['funds'][0]['date']==JUN
    assert d['administrators'][0]['mom']==0


def test_report_directory_uses_active_nonredeemable_without_any_valid_nav(monkeypatch):
    from importlib import import_module
    router=import_module('src.api.industry_workspace.router')
    calls=[]
    monkeypatch.setattr(router,'rows',lambda sql,p:calls.append((sql,p)) or [])
    assert report_data(SEP,CUT,'fm')==([],[]) and not calls
    report_data(SEP,CUT)
    sql=calls[0][0]
    assert 'f.vigente AND f.rescatable IS FALSE' in sql
    assert 'NOT EXISTS' in sql and 'valores_cuota_fi' in sql
