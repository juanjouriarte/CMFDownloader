from datetime import date
import pytest
from src.api.industry_workspace.portfolio_debt import terms, profile, instrument_terms, maturity_bucket, maturity_date

D = date(2026, 9, 30)
def row(**kw):
    return dict(dict(instrument_type='BCU', weight=10, quantity_unit='UF', currency_code='$$',
        maturity_date='30/09/2027', reported_yield='3.5', valuation_code='1', rate_base='365', interest_type='RC'), **kw)

@pytest.mark.parametrize('code', ['2', '3', None, ''])
def test_prices_and_par_are_never_yield(code):
    assert terms(row(valuation_code=code, reported_yield=102))['yield_pct'] is None

@pytest.mark.parametrize('v', [0, -0.3, 4.2])
def test_zero_and_negative_yields_are_valid(v):
    assert terms(row(reported_yield=v))['yield_pct'] == v

@pytest.mark.parametrize('v', ['nan', 'inf', None, 'bad'])
def test_invalid_yield(v):
    assert terms(row(reported_yield=v))['yield_pct'] is None

def test_fund_units_and_unknown_types_do_not_become_debt():
    assert terms(row(instrument_type='CFI'))['is_debt'] is False
    assert terms(row(instrument_type='CFI'))['yield_pct'] is None
    assert terms(row(instrument_type='OIED'))['is_debt'] is None

def test_rate_groups_preserve_units_basis_and_type():
    p = profile([row(), row(quantity_unit='$$'), row(rate_base='30'), row(interest_type='NC'),
                 row(rate_base='0'), row(interest_type='NA'), row(quantity_unit='???')], D)
    assert len(p['yield_groups']) == 4
    assert p['yield_weight'] == 40
    assert {g['key'] for g in p['yield_groups']} == {'UF:RC:365','CLP:RC:365','UF:RC:30','UF:NC:365'}
    assert terms(row(quantity_unit='USD'))['rate_currency'] == 'USD_SEGURO'

def test_dates_and_maturity_use_selected_period():
    for invalid in ['99999999', '00000000', '31/02/2026', '19000101', date(9999,1,1)]:
        assert maturity_date(invalid) is None
    assert maturity_date('20270930') == date(2027,9,30)
    for dt, bucket in [('29/09/2026','past'), ('30/09/2026','m3'), ('30/12/2026','m3'),
                       ('31/12/2026','y1'), ('30/09/2027','y1'), ('01/10/2027','y3')]:
        assert maturity_bucket(terms(row(maturity_date=dt)), D) == bucket
    assert maturity_bucket(terms(row(instrument_type='BBP')), D) == 'perpetual'

def test_duplicate_terms_only_average_comparable_yields():
    t = instrument_terms([row(weight=10, reported_yield=2),row(weight=30, reported_yield=4)])
    assert t['yield_pct'] == 3.5 and t['yield_status'] == 'weighted'
    t = instrument_terms([row(),row(rate_base=30)])
    assert t['yield_pct'] is None and t['yield_status'] == 'mixed'
    t = instrument_terms([row(),row(maturity_date='30/09/2028')])
    assert t['maturity_date'] is None and t['maturity_status'] == 'mixed'
    t = instrument_terms([row()], observed=False)
    assert t['maturity_status'] == 'partial' and t['yield_pct'] is None
    assert instrument_terms([]) is None

def test_profiles_keep_missingness_and_reconcile_weight():
    assert profile([],D) is None
    p = profile([row(),row(maturity_date=None),row(maturity_date='01/01/2026'),
                 row(instrument_type='BBP'),row(instrument_type='ACC'),row(instrument_type='???'),
                 row(weight='nan')],D)
    assert p['debt_weight'] == 40
    assert sum(b['weight'] for b in p['maturity_buckets']) == 40
    assert p['maturity_weight'] == 10 and p['average_maturity_years'] == pytest.approx(365/365.25)
    assert p['non_debt_weight'] == p['unclassified_weight'] == 10
    assert p['invalid_weights'] == 1

def test_history_aligns_terms_and_profiles_and_does_not_fill_report_gaps():
    from src.api.industry_workspace.portfolio_history import build_history
    dates=[date(2026,8,31),D,date(2026,10,31)]
    r=dict(row(),periodo=dates[0],source='cartera_naci',kind='fm',run='1',
           identifier='BCU1',issuer_rut='12345',issuer='Banco')
    result=build_history([r,dict(r,periodo=dates[2])],dates,dates)
    assert result['instruments'][0]['debt_terms'][1] is None
    assert result['debt_profiles'][1] is None
    assert result['debt_profiles'][0]['average_maturity_years']>result['debt_profiles'][2]['average_maturity_years']
