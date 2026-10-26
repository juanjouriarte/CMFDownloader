from datetime import date
from decimal import Decimal

from src.api.insurance_history import build_history, fund_share, load_bases, month_end

AUG=date(2026,8,1)
SEP=date(2026,9,1)


def holding(period, value=100, insurer='1', **kwargs):
    return dict(period=period, value_clp=Decimal(value), insurer_rut=insurer,
        fund_key='fm:123',fund_kind='fm',fund_run='123',name='Fund',
        tipo='Deuda',grupo='Nacional',category='Corto plazo',administrator_rut='9',administrator_name='AGF',
        currency='CLP',series=['A'],unvalued_positions=0,
        share_positions=[dict(series='A',units=10)], **kwargs)


def reports(*periods):
    return [dict(period=p,insurer_rut='1',family=f,status='validated') for p in periods for f in ('F','X')]


def base(period=SEP, currency='USD', value=1000, price=10):
    return dict(parts=[dict(currency=currency,value=Decimal(value),date=month_end(period))],complete=True,
                series=[dict(serie='A',price=Decimal(price),aum=Decimal(value),currency=currency)])


def test_series_nav_percentage_uses_native_currency_not_clp_valuation():
    row=holding(SEP, value=999999)
    row['share_positions']=[dict(series='A',units=10),dict(series='A',units=5),dict(series='B',units=2)]
    nav=base(value=2000)
    nav['series'].append(dict(serie='B',price=Decimal(25),aum=Decimal(1000),currency='USD'))
    share,source,reason=fund_share([row],nav,{},SEP,True)
    assert share==10 and source=='units_nav_estimate' and reason is None
    assert fund_share([row],base(),{'1':Decimal('12.3')},SEP,True)[:2]==(Decimal('12.3'),'reported_shareholders')
    assert fund_share([row],nav,{'1':Decimal('12.3')},SEP,False)[0]==10


def test_no_stale_fx_mixed_currency_or_out_of_range_ownership():
    row=holding(SEP)
    assert fund_share([row],None,{},SEP,True)[2]=='no_fund_nav'
    assert fund_share([row],base(AUG),{},SEP,True)[2]=='different_nav_date'
    nav=base();nav['parts'].append(dict(currency='CLP',value=100,date=month_end(SEP)))
    assert fund_share([row],nav,{},SEP,True)[2]=='mixed_nav_currencies'
    row['share_positions']=[dict(series='UNKNOWN',units=10)]
    assert fund_share([row],base(),{},SEP,True)[2]=='unmatched_series_or_currency'
    assert fund_share([row],base(currency='CLP'),{},SEP,True)[:2]==(Decimal(10),'clp_valuation_estimate')
    row['share_positions']=[dict(series='A',units=200)]
    assert fund_share([row],base(),{},SEP,True)[2]=='inconsistent_share'
    nav=base();nav['complete']=False
    assert fund_share([row],nav,{},SEP,True)[2]=='incomplete_nav'


def test_history_exact_previous_month_partial_reports_and_loaded_range():
    loaded=[AUG,SEP]
    rows=[holding(AUG,100),holding(SEP,120)]
    d=build_history(rows,reports(*loaded),loaded,date(2021,10,1),SEP,{}, {})
    assert d['dates']==loaded and d['requested_months']==60 and d['loaded_months']==2
    assert d['rows'][0]['current']['delta_clp']==20
    assert d['rows'][0]['current']['delta_pct']==20
    assert d['rows'][0]['points'][0]['delta_clp'] is None
    partial=reports(*loaded);partial[-1]['status']='quarantined'
    d=build_history(rows,partial,loaded,AUG,SEP,{}, {})
    assert d['rows'][0]['current']['value_clp']==120
    assert d['rows'][0]['current']['delta_clp'] is None
    assert not d['rows'][0]['current']['complete']


def test_one_month_fetches_baseline_and_retains_absent_funds_without_invented_zero():
    rows=[holding(AUG,100)]
    d=build_history(rows,reports(AUG,SEP),[AUG,SEP],SEP,SEP,{}, {})
    assert d['dates']==[SEP]
    p=d['rows'][0]['current']
    assert p['value_clp']==0 and not p['observed'] and p['delta_pct']==-100
    d=build_history(rows,reports(AUG),[AUG,SEP],SEP,SEP,{}, {})
    assert d['rows'][0]['current']['value_clp'] is None
    assert d['rows'][0]['current']['delta_clp'] is None


def test_gap_is_retained_and_never_bridged_or_called_a_zero():
    oct=date(2026,10,1)
    d=build_history([holding(AUG,100),holding(oct,130)],reports(AUG,oct),[AUG,oct],AUG,oct,{}, {})
    assert d['dates']==[AUG,SEP,oct]
    assert d['rows'][0]['points'][1]['value_clp'] is None
    assert d['rows'][0]['current']['delta_clp'] is None


def test_all_insurers_are_summed_but_fund_nav_is_only_once():
    rows=[holding(SEP,100),holding(SEP,200,insurer='2')]
    rr=reports(SEP)+[dict(period=SEP,insurer_rut='2',family=f,status='validated') for f in ('F','X')]
    d=build_history(rows,rr,[SEP],SEP,SEP,{('fm','123',SEP):base(currency='CLP')}, {})
    p=d['rows'][0]['current']
    assert p['value_clp']==300 and p['insurers']==2
    assert p['fund_aum'][0]['value']==1000
    assert p['fund_share_pct']==20  # 10 units per insurer * price 10 / 1000.


def test_incomplete_exact_nav_does_not_fall_back_to_stale_aum_or_zero():
    def read(sql, params):
        if 'mv_industry_monthly' in sql:
            return [dict(run_fondo='123',month=SEP,currency='USD',aum=1000,latest_data_date=date(2026,9,29))]
        return [dict(run_fondo='123',fecha=month_end(SEP),serie='A',currency='USD',
                     aum=Decimal('NaN'),price=10)]
    bases, _ = load_bases(read,[holding(SEP)],[SEP])
    nav = bases['fm','123',SEP]
    assert not nav['complete']
    assert nav['parts'][0]['value'] is None
    assert fund_share([holding(SEP)],nav,{},SEP,True)[2]=='incomplete_nav'


def test_fund_investors_group_series_and_keep_insurer_denominators_and_coverage_separate():
    from src.api.insurance_investors import add_investors
    records = [holding(AUG,100),holding(SEP,60),holding(SEP,60),
               holding(AUG,200,insurer='2'),holding(SEP,300,insurer='2')]
    other = holding(SEP,120);other.update(fund_key='fm:456',fund_run='456')
    records.append(other)
    for r in records:r['insurer_name']='Insurer '+r['insurer_rut']
    coverage=reports(AUG,SEP)+[dict(period=p,insurer_rut='2',family=f,status='validated') for p in (AUG,SEP) for f in ('F','X')]
    coverage[-1]['status']='quarantined'
    data=build_history(records,coverage,[AUG,SEP],SEP,SEP,{}, {})
    add_investors(data,records,coverage,[AUG,SEP],SEP,SEP)
    fund=next(r for r in data['rows'] if r['key']=='fm:123')
    assert fund['current']['value_clp']==420
    one=next(r for r in fund['investors'] if r['rut']=='1')['current']
    two=next(r for r in fund['investors'] if r['rut']=='2')['current']
    assert one['value_clp']==120 and one['delta_clp']==20
    assert one['insurer_scope_value_clp']==240 and one['insurer_scope_pct']==50
    assert two['value_clp']==300 and two['delta_clp'] is None
    assert two['insurer_scope_pct']==100 and not two['complete']
    assert fund['current']['delta_clp'] is None


def test_fund_investors_missing_valuation_does_not_invent_a_weight():
    from src.api.insurance_investors import add_investors
    rows=[holding(SEP,50),holding(SEP,100)]
    rows[1].update(fund_key='fm:456',fund_run='456',unvalued_positions=1)
    for r in rows:r['insurer_name']='One'
    data=build_history(rows,reports(SEP),[SEP],SEP,SEP,{}, {})
    add_investors(data,rows,reports(SEP),[SEP],SEP,SEP)
    for row in data['rows']:
        assert row['investors'][0]['current']['insurer_scope_pct'] is None


def test_recovered_foreign_file_allows_comparison_but_partial_control_is_not_holdings():
    rr = reports(AUG, SEP)
    for record in rr:
        record['status'] = 'accepted_with_warning'
    rows = [holding(AUG, 100), holding(SEP, 120)]
    result = build_history(rows, rr, [AUG, SEP], AUG, SEP, {}, {})
    assert result['rows'][0]['current']['complete']
    assert result['rows'][0]['current']['delta_clp'] == 20
    rr[-1]['status'] = 'partially_reconciled'
    result = build_history(rows, rr, [AUG, SEP], AUG, SEP, {}, {})
    assert not result['rows'][0]['current']['complete']
    assert result['rows'][0]['current']['delta_clp'] is None
