from datetime import date
from decimal import Decimal
import os
import pytest
from src.etl.investmentFunds.reported_equity import parse_report, report_link, recent_quarters, InvalidReport

PERIOD=date(2026,6,30)


def test_history_quarters_exclude_open_quarter_and_respect_bounds():
    from src.etl.investmentFunds.reported_equity import quarter_range
    assert quarter_range(date(2019,12,31),date(2020,12,31),date(2020,10,1))==[
        date(2019,12,31),date(2020,3,31),date(2020,6,30),date(2020,9,30)]
    assert quarter_range(date(2020,3,31),date(2020,3,31),date(2020,3,31))==[]
    with pytest.raises(ValueError):quarter_range(date(2021,1,1),date(2020,1,1))


def test_history_import_resumes_missing_and_preserves_validation_failures(monkeypatch):
    from types import SimpleNamespace
    from src.etl.investmentFunds import reported_equity as module
    monkeypatch.setattr(module,'eligible_funds',lambda today:[dict(run_fondo='1')])
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    called=[];saved=[];outcomes=[]
    def page(http,run,period):
        called.append(period)
        return SimpleNamespace(url='source'),None if period.month==6 else 'xml'
    def report(http,link,run,period,referer):
        if period.month==9:raise module.InvalidReport('Fund identity mismatch')
        return dict(run_fondo=run,periodo=period,currency='USD',equity=Decimal(123))
    monkeypatch.setattr(module,'fetch_report_page',page)
    monkeypatch.setattr(module,'fetch_report',report)
    monkeypatch.setattr(module,'save_report',lambda row:saved.append(row))
    result=module.ReportedEquityDownloader().run(from_date=date(2019,12,31),to_date=date(2020,9,30),
        completed_missing={('1',date(2019,12,31))},on_result=outcomes.append,workers=1)
    assert result.downloaded==result.errors==1 and result.skipped==2
    assert len(saved)==1 and saved[0]['periodo']==date(2020,3,31)
    assert len(called)==3
    assert [r['status'] for r in outcomes]==['resumed_missing','saved','unavailable','error']


def test_backfill_journal_only_resumes_confirmed_missing(tmp_path):
    from scripts.backfill_fi_reported_equity import missing_from_journal
    path=tmp_path/'progress.jsonl'
    path.write_text('''{"run":"1","period":"2020-03-31","status":"unavailable"}
{"run":"1","period":"2020-03-31","status":"saved"}
{"run":"2","period":"2020-03-31","status":"unavailable"}
{"run":"3","period":"2020-03-31","status":"error"}
{"run":"4","period":"2020-03-31","status":''')
    assert missing_from_journal(path)=={('2',date(2020,3,31))}


def xml(**values):
    accounts=dict(TotalActivo='540000',TotalPatrimonioNeto='539988',TotalPasivoCorriente='12',TotalPasivoNoCorriente='0')
    accounts.update(values)
    return ('''<IFRS><Identificacion><RUTFondoInforma>10069</RUTFondoInforma></Identificacion>
      <DatosPeriodo><MonedaPresentacionEstadosFinancieros>PROM</MonedaPresentacionEstadosFinancieros>
      <PeriodoPresentacionEstadosFinancieros><Mes>06</Mes><Anio>2026</Anio></PeriodoPresentacionEstadosFinancieros></DatosPeriodo>
      <Contextos><PeriodoActual><FechaTermino>2026-06-30</FechaTermino></PeriodoActual></Contextos>'''+
      ''.join(f'<Cuenta CodigoCuenta="{k}" Context="PeriodoActual">{v}</Cuenta>' for k,v in accounts.items())+
      '<Cuenta CodigoCuenta="TotalPatrimonioNeto" Context="PeriodoAnualAnterior">123</Cuenta></IFRS>').encode()


def html_statement(**values):
    accounts={'Total Activo':'540.000','Total Patrimonio Neto':'539.988',
              'Total Pasivo Corriente':'12','Total Pasivo No Corriente':'0','Total Pasivo':'540.000'}
    accounts.update(values)
    return ('''<dl id="datos_ent"><dt>RUT:</dt><dd>10069-1</dd></dl><table>
       <tr><th colspan="2">ESTADO DE SITUACION FINANCIERA ("Expresado en miles de Dolar ")</th>
       <th>Nota N°</th><th>Al 30/06/2026</th><th>Al 31/12/2025</th></tr>'''+
       ''.join(f'<tr><td></td><td>{k} (+)</td><td>17</td><td>{v}</td><td>999.999</td></tr>' for k,v in accounts.items())+
       '<tr><td colspan="10">Las Notas adjuntas forman parte integral de estos estados financieros.</td></tr></table>').encode()


def test_displayed_statement_matches_xml_and_ignores_notes_and_comparative_values():
    from src.etl.investmentFunds.reported_equity import parse_html_report
    html=parse_html_report(html_statement(),'10069',PERIOD)
    original=parse_report(xml(),'10069',PERIOD)
    for key in ['run_fondo','periodo','currency','equity','assets','liabilities','source_scale']:
        assert html[key]==original[key]
    negative=parse_html_report(html_statement(**{'Total Activo':'0','Total Patrimonio Neto':'-1',
        'Total Pasivo Corriente':'1','Total Pasivo':'0'}),'10069',PERIOD)
    assert negative['equity']==-1000  # Retained, but still excluded by Panorama.


def test_html_two_row_date_header_still_requires_current_period():
    from src.etl.investmentFunds.reported_equity import parse_html_report
    content=html_statement().replace(b'<th colspan="2">',b'<th colspan="2" rowspan="2">').replace(
        'Nota N°</th><th>Al 30/06/2026</th><th>Al 31/12/2025</th></tr>'.encode(),
        'Nota N°</th><th colspan="2">PERIODOS</th></tr><tr><th>Al 30/06/2026</th><th>Al 31/12/2025</th></tr>'.encode())
    assert parse_html_report(content,'10069',PERIOD)['equity']==539988000
    with pytest.raises(InvalidReport):parse_html_report(content,'10069',date(2025,12,31))


@pytest.mark.parametrize('content',[
    html_statement().replace(b'10069-1',b'10070-1'),
    html_statement().replace(b'Al 30/06/2026',b'Al 31/03/2026'),
    html_statement().replace(b'miles',b'unidades'),
    html_statement().replace(b'Dolar',b'Unknown'),
    html_statement(**{'Total Patrimonio Neto':'540.000'}),
    html_statement(**{'Total Activo':'540.00'}),
    html_statement(**{'Total Pasivo':'123'}),
    html_statement().replace(b'Total Pasivo No Corriente',b'Total Pasivo Corriente'),
])
def test_displayed_statement_rejects_identity_date_units_and_account_errors(content):
    from src.etl.investmentFunds.reported_equity import parse_html_report
    with pytest.raises(InvalidReport):parse_html_report(content,'10069',PERIOD)


def test_html_fallback_only_for_unavailable_xml_not_invalid_xml(monkeypatch):
    from types import SimpleNamespace
    from src.etl.investmentFunds import reported_equity as module
    page=SimpleNamespace(url='source',content=html_statement())
    def blocked(*a):raise module.XMLDownloadUnavailable('CMF unavailable')
    monkeypatch.setattr(module,'fetch_report',blocked)
    row=module.fetch_statement(None,page,'xml','10069',PERIOD)
    assert row['equity']==539988000 and 'mm=06&aa=2026' in row['source_url']
    def invalid(*a):raise InvalidReport('Fund identity mismatch')
    monkeypatch.setattr(module,'fetch_report',invalid)
    with pytest.raises(InvalidReport,match='identity mismatch'):
        module.fetch_statement(None,page,'xml','10069',PERIOD)


def test_reported_net_equity_not_assets_and_scale():
    row=parse_report(xml(),'10069',PERIOD)
    assert row['equity']==Decimal('539988000')
    assert row['assets']==Decimal('540000000')
    assert row['liabilities']==12000
    assert row['currency']=='USD' and row['source_scale']==1000
    assert recent_quarters(date(2026,1,1))==[date(2025,12,31),date(2025,9,30)]


def test_legacy_undeclared_latin1_without_relaxing_xml_or_financial_validation():
    legacy=xml().replace(b'</Identificacion>',b'<NombreEntidadInforma>ESPA\xd1A</NombreEntidadInforma></Identificacion>')
    assert parse_report(legacy,'10069',PERIOD)['equity']==539988000
    with pytest.raises(InvalidReport):
        parse_report(b'<?xml version="1.0" encoding="UTF-8"?>'+legacy,'10069',PERIOD)
    with pytest.raises(InvalidReport):parse_report(legacy.replace(b'</IFRS>',b''),'10069',PERIOD)
    with pytest.raises(InvalidReport):parse_report(legacy,'10070',PERIOD)
    with pytest.raises(InvalidReport):parse_report(legacy,'10069',date(2026,3,31))


@pytest.mark.parametrize('content,run,period',[
    (xml(),'10070',PERIOD),(xml(),'10069',date(2026,9,30)),
    (xml(TotalPatrimonioNeto='540000'),'10069',PERIOD),
    (xml(TotalPatrimonioNeto='NaN'),'10069',PERIOD),
    (xml().replace(b'PROM',b'UNKNOWN'),'10069',PERIOD),
    (xml().replace(b'</IFRS>',b'<Cuenta CodigoCuenta="TotalPatrimonioNeto" Context="PeriodoActual">539988</Cuenta></IFRS>'),'10069',PERIOD),
    (b'<!DOCTYPE IFRS>'+xml(),'10069',PERIOD),
])
def test_reject_mismatched_ambiguous_or_invalid_reports(content,run,period):
    with pytest.raises(InvalidReport):parse_report(content,run,period)


def test_discovery_requires_explicit_scale_identity_and_official_host():
    link='https://www.cmfchile.cl/institucional/inc/inf_financiera/ifrs_xml/ifrs_xml_verarchivo.php?archivo=a.xml&amp;rut=10069&amp;periodo=202606&amp;desc_archivo=Estados_financieros_'
    html=f'<p>Expresado en miles de Dolar</p><a href="{link}">Descarga</a>'
    assert report_link(html,'10069',PERIOD).startswith('https://www.cmfchile.cl/')
    assert report_link('<p>No existe información</p>','10069',PERIOD) is None
    assert report_link('<p>Sin información.</p>','10069',PERIOD) is None
    for bad in [html.replace('miles','unidades'),html.replace('www.cmfchile.cl','evil.com'),'<h1>403</h1>']:
        with pytest.raises(InvalidReport):report_link(bad,'10069',PERIOD)


def test_fallback_dates_values_and_provenance():
    from src.api.reported_equity import select_reports
    from src.api.fx import convert_aum,aggregate
    from src.fx import Rates
    candidates=[dict(run_fondo=str(i),name=str(i),fund_type='fi',aum=None) for i in range(5)]
    base=dict(periodo=PERIOD,currency='CLP',equity=Decimal(150),source_url='https://www.cmfchile.cl/')
    reports={'0':base,'1':dict(base,periodo=date(2025,12,31)),
             '2':dict(base,equity=Decimal(-1)),'3':dict(base,periodo=date(2026,12,31))}
    included,excluded=select_reports(candidates,reports,date(2026,10,27))
    assert len(included)==1 and included[0]['aum_source']=='quarterly_report'
    assert {r['reason'] for r in excluded}=={'missing_report','stale_report','invalid_equity','future_report'}
    native=dict(run_fondo='9',fund_type='fi',currency='CLP',aum=Decimal(100),data_date=date(2026,10,27))
    converted,_=convert_aum([native,*included],Rates([]))
    total=aggregate(converted)
    assert total['aum']==250 and total['nav_aum']==100 and total['reported_aum']==150
    assert total['nav_funds']==total['reported_funds']==1
    assert total['nav_date']==date(2026,10,27) and total['first_report_date']==PERIOD


def test_database_eligibility_without_portfolio_and_nav_precedence():
    if os.getenv('RUN_FX_DB_TESTS')!='1':pytest.skip('Opt-in local database check')
    from sqlalchemy import text
    from src.db.engine import engine
    from src.etl.investmentFunds.reported_equity import ELIGIBLE_SQL
    assert engine.url.host in ('localhost','127.0.0.1','::1')
    with engine.connect() as conn:
        with conn.begin():
            # Temporary tables shadow public tables only within this connection.
            conn.execute(text('CREATE TEMP TABLE fondos_inversion(run_fondo TEXT,vigente BOOLEAN,rescatable BOOLEAN) ON COMMIT DROP'))
            conn.execute(text('CREATE TEMP TABLE valores_cuota_fi(run_fondo TEXT,fecha DATE,patrimonio_neto NUMERIC) ON COMMIT DROP'))
            conn.execute(text("INSERT INTO fondos_inversion VALUES ('no_portfolio',true,false),('has_nav',true,false),('rescatable',true,true),('inactive',false,false),('unknown',true,null),('invalid_nav',true,false),('future_nav',true,false),('zero_nav',true,false)"))
            conn.execute(text("INSERT INTO valores_cuota_fi VALUES ('has_nav','2026-01-01',100),('invalid_nav','2026-01-01','NaN'),('future_nav','2027-01-01',100),('zero_nav','2026-01-01',0)"))
            runs={r.run_fondo for r in conn.execute(text(ELIGIBLE_SQL),dict(today=date(2026,10,27)))}
            assert runs=={'no_portfolio','invalid_nav','future_nav'}


def test_temporary_cmf_block_retried_without_relaxing_validation(monkeypatch):
    from types import SimpleNamespace
    from src.etl.investmentFunds import reported_equity as module
    responses=iter([b'ACCION NO PERMITIDA 16',xml()])
    monkeypatch.setattr(module,'fetch',lambda *a,**k:SimpleNamespace(content=next(responses)))
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert module.fetch_report(None,'source','10069',PERIOD,'report')['equity']==539988000
    monkeypatch.setattr(module,'fetch',lambda *a,**k:SimpleNamespace(content=b'ACCION NO PERMITIDA 16'))
    with pytest.raises(InvalidReport,match='after 3 attempts'):
        module.fetch_report(None,'source','10069',PERIOD,'report')


def test_report_page_retries_unknown_response_but_not_explicit_absence(monkeypatch):
    from types import SimpleNamespace
    from src.etl.investmentFunds import reported_equity as module
    calls=[]
    pages=iter(['ACCION NO PERMITIDA 16','No existe información'])
    def fetch(*a,**k):
        calls.append(k)
        return SimpleNamespace(text=next(pages),url='https://www.cmfchile.cl/report')
    monkeypatch.setattr(module,'fetch',fetch)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert module.fetch_report_page(None,'10069',PERIOD)[1] is None
    assert len(calls)==2
    assert calls[0]['params']['tipoentidad']=='FINRE'


def test_consolidated_source_views_recompute_totals_shares_and_fx(monkeypatch):
    from src.api import fx
    from src.fx import Rates
    base=dict(fund_type='fi',administrator='A',category_type='Deuda',category_group='Nacional',category_name='Deuda',currency='CLP')
    nav=dict(base,run_fondo='1',name='NAV',aum=Decimal(100),data_date=date(2026,10,27))
    candidate=dict(base,run_fondo='2',name='EEFF',aum=None,data_date=None)
    report=dict(candidate,administrator='B',aum=Decimal(300),data_date=PERIOD,aum_source='quarterly_report',source_url='https://www.cmfchile.cl/')
    monkeypatch.setattr(fx,'_rows',lambda sql,params=None: [nav,candidate] if 'snapshot' in sql else [dict(cutoff=date(2026,10,27))])
    monkeypatch.setattr(fx,'supplement',lambda snapshot,cutoff:([report],[]))
    monkeypatch.setattr(fx.Rates,'load',lambda *a:Rates([]))
    all_rows=fx.consolidated(None,fund_type='fi',aum_source='all')
    nav_rows=fx.consolidated(None,fund_type='fi',aum_source='nav')
    reports=fx.consolidated(None,fund_type='fi',aum_source='quarterly_report')
    assert all_rows['total']['aum']==nav_rows['total']['aum']+reports['total']['aum']==400
    assert [r['share'] for r in all_rows['administrators']]==[75,25]
    for response,source,amount in [(nav_rows,'nav',100),(reports,'quarterly_report',300)]:
        assert response['aum_source']==source and response['total']['funds']==1
        assert response['administrators'][0]['share']==100
        assert response['categories'][0]['aum']==amount
        assert {r['aum_source'] for r in response['fx']}=={source}
    assert reports['flow']['points']==0 and reports['flow']['amount'] is None
    assert fx.consolidated(None,fund_type='fi',include_reported_equity=False)['total']['aum']==100


def test_native_panorama_enriches_currency_before_filtering_and_never_needs_fx(monkeypatch):
    from src.api import fx
    base=dict(fund_type='fi',administrator='A',category_type='Deuda',category_group='Nacional',category_name='Local')
    nav=dict(base,run_fondo='1',name='NAV USD',currency='USD',aum=Decimal(100),data_date=date(2026,10,27))
    clp=dict(nav,run_fondo='2',name='NAV CLP',currency='CLP',aum=Decimal(9999))
    candidate=dict(base,run_fondo='3',name='No NAV',currency=None,aum=None,data_date=None)
    report=dict(candidate,currency='USD',aum=Decimal(300),data_date=PERIOD,aum_source='quarterly_report',source_url='https://www.cmfchile.cl/')
    other=dict(report,run_fondo='4',category_group='Extranjero',aum=Decimal(50))
    monkeypatch.setattr(fx,'_rows',lambda sql,params=None: [nav,clp,candidate] if 'snapshot' in sql else [dict(cutoff=date(2026,10,27))])
    monkeypatch.setattr(fx,'supplement',lambda snapshot,cutoff:([report,other],[]))
    def no_fx(*args):raise AssertionError('Native amounts must not depend on stored FX')
    monkeypatch.setattr(fx.Rates,'load',no_fx)
    result=fx.consolidated(None,currency='USD',nombre_cat='Local',category_type='Deuda',category_group='Nacional')
    assert result['mode']=='native' and result['currency']=='USD' and result['fx']==[]
    assert result['total']['aum']==400 and result['total']['reported_aum']==300
    assert result['total']['funds']==2 and result['administrators'][0]['share']==100
    assert result['categories'][0]['category_group']=='Nacional'
    assert len(result['options'])==3  # Options allow switching category without clearing source/currency.
    fund=result['reported_equity']['funds'][0]
    assert fund['currency']=='USD' and fund['aum']==fund['native_aum']==300
    assert fund['aum_clp'] is None
    assert fx.consolidated(None,currency='USD',aum_source='nav')['total']['aum']==100
    assert fx.consolidated(None,currency='USD',aum_source='quarterly_report')['total']['aum']==350
    assert fx.consolidated(None,currency='EUR')['total']['aum'] is None


def test_reported_history_uses_actual_closes_only_and_preserves_native_units(monkeypatch):
    from src.api import fx
    records=[dict(fund_type='fi',run_fondo='1',currency='USD',aum=Decimal(120),
                  data_date=PERIOD,month=date(2026,6,1),aum_source='quarterly_report',
                  administrator='A',category_name='Deuda',category_type='Deuda',category_group='Nacional')]
    monkeypatch.setattr(fx,'_rows',lambda sql,params=None: records if 'WITH eligible' in sql else [dict(cutoff=date(2026,10,27))])
    result=fx.reported_evolution(None,currency='USD')
    assert len(result)==1 and result[0]['month']==date(2026,6,1)
    assert result[0]['aum']==120 and result[0]['nav_funds']==0 and result[0]['reported_funds']==1
    assert fx.reported_evolution(None,currency='USD',admin='B')==[]
    assert fx.reported_evolution(None,fund_type='fm')==[]


def test_latest_report_months_selects_each_fund_without_future_stale_or_currency_fallback():
    from src.api.fx import latest_report_months
    def row(run,day,value,currency='USD'):
        return dict(run_fondo=run,data_date=date.fromisoformat(day),aum=Decimal(value),currency=currency)
    rows=[row('1','2026-03-31',100),row('1','2026-06-30',120),
          row('2','2026-03-31',50),row('2','2026-06-30',-1),
          row('3','2026-03-31',70),row('3','2026-06-30',80000,'CLP'),
          row('4','2026-12-31',999),row('5','2025-12-31',20)]
    points=latest_report_months(rows,date(2026,5,1),date(2026,10,27),'USD')
    may=[r for r in points if r['month']==date(2026,5,1)]
    assert sum(r['aum'] for r in may)==240
    october=[r for r in points if r['month']==date(2026,10,1)]
    assert len(october)==1 and october[0]['aum']==120
    assert october[0]['data_date']==PERIOD and october[0]['as_of']==date(2026,10,27)
    assert all(r['run_fondo']!='4' for r in points)


def test_latest_report_api_current_month_matches_snapshot_and_keeps_report_fx_date(monkeypatch):
    from src.api import fx
    from src.fx import Rates
    base=dict(fund_type='fi',run_fondo='1',currency='USD',aum=Decimal(120),data_date=PERIOD,
              month=date(2026,6,1),aum_source='quarterly_report',administrator='A',category_name='Debt')
    monkeypatch.setattr(fx,'_rows',lambda sql,params=None:[base] if 'WITH eligible' in sql else [dict(cutoff=date(2026,10,27))])
    monkeypatch.setattr(fx.Rates,'load',lambda *a:Rates([
        dict(currency='USD',date=PERIOD,clp_per_unit=Decimal(900),series_code='usd'),
        dict(currency='USD',date=date(2026,10,27),clp_per_unit=Decimal(1000),series_code='usd')]))
    result=fx.reported_evolution(None,from_date=date(2026,5,1),basis='latest')
    assert result[0]['aum'] is None and result[0]['funds']==0
    last=result[-1]
    assert last['as_of']==date(2026,10,27) and last['carried_funds']==1
    assert last['aum']==108000 and last['first_report_date']==PERIOD
    assert last['first_fx_date']==PERIOD

@pytest.mark.parametrize('has_nav,period,equity,included,reason',[
    (False,date(2026,6,30),Decimal(100),True,None),
    (False,date(2025,12,31),Decimal(100),False,'stale_report'),
    (False,date(2026,6,30),Decimal(-1),False,'invalid_equity'),
    (True,date(2026,6,30),Decimal(100),False,'not_eligible'),
])
def test_fund_statement_history_preserved_even_when_not_in_panorama(monkeypatch,has_nav,period,equity,included,reason):
    from src.api import reported_equity as module
    report=dict(periodo=period,currency='USD',equity=equity,assets=Decimal(110),
                liabilities=Decimal(110)-equity,source_url='https://www.cmfchile.cl/report')
    def rows(sql,params=None):
        if 'FROM fondos_inversion' in sql:return [dict(vigente=True,rescatable=False,has_nav=has_nav)]
        if 'FROM fi_reported_equity' in sql:return [report]
        return [dict(cutoff=date(2026,10,27))]
    monkeypatch.setattr(module,'_rows',rows)
    result=module.financial_statements('1',None)
    assert result['reports']==[report]  # Native amounts/currency and excluded reports remain inspectable.
    assert result['included_in_panorama'] is included
    assert result['exclusion_reason']==reason
    assert result['panorama_report_date']==(period if included else None)


def test_fund_statement_missing_fund_is_404(monkeypatch):
    from fastapi import HTTPException
    from src.api import reported_equity as module
    monkeypatch.setattr(module,'_rows',lambda *a:[])
    with pytest.raises(HTTPException) as exc:module.financial_statements('missing',None)
    assert exc.value.status_code==404


def test_research_directory_opt_in_preserves_nav_universe_and_precedence(monkeypatch):
    import importlib
    module=importlib.import_module('src.api.industry_workspace.router')
    from src.api import reported_equity
    nav=dict(kind='fi',run='1',currency='CLP',aum=100)
    added=dict(kind='fi',run='2',currency='USD',aum=-1,aum_source='quarterly_report')
    monkeypatch.setattr(module,'rows',lambda *a:[dict(nav)])
    monkeypatch.setattr(reported_equity,'reported_fund_directory',lambda:[dict(nav,currency='USD'),added])
    assert module.funds()==[nav]
    assert module.funds(include_reported=True)==[nav,added]
