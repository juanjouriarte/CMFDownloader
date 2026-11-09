from datetime import date
from decimal import Decimal

import pytest

from src.api.industry_workspace import administrator_latest as latest
from src.api.industry_workspace.administrator_equity import analyze
from src.fx import Rates


FM_DATE = date(2026, 11, 5)
FI_DATE = date(2026, 11, 6)
REPORT_DATE = date(2026, 9, 30)
MONTH = date(2026, 11, 1)


def universe_row(run, kind, amount, day, currency='USD'):
    return dict(run_fondo=run, fund_type=kind, name=run, administrator='BTG',
                category_name='Deuda', aum=amount, data_date=day, currency=currency)


@pytest.fixture
def shared_snapshot(monkeypatch):
    universe = [universe_row('fm', 'fm', Decimal(10), FM_DATE),
                universe_row('fi', 'fi', Decimal(20), FI_DATE),
                universe_row('q', 'fi', None, None)]
    calls = []
    def rows(sql, params=None):
        calls.append((sql, params))
        return universe if params else [dict(cutoff=FI_DATE)]
    def supplement(snapshot, cutoff):
        assert snapshot is universe and cutoff == FI_DATE
        return [dict(universe[2], aum=Decimal(30), data_date=REPORT_DATE,
                     aum_source='quarterly_report')], []
    monkeypatch.setattr(latest, '_rows', rows)
    monkeypatch.setattr(latest, 'supplement', supplement)
    return calls


@pytest.mark.parametrize('source,runs', [('all', {'fm','fi','q'}), ('nav', {'fm','fi'}),
                                      ('quarterly_report', {'q'})])
def test_reuses_panorama_selection_and_keeps_each_actual_date(shared_snapshot, source, runs):
    result = latest.snapshot('all', source)
    assert {r['run_fondo'] for r in result['rows']} == runs
    assert result['reference_dates'] == dict(fm=FM_DATE, fi=FI_DATE)
    assert result['date'] == FI_DATE
    assert {r['run_fondo']: r['valuation_date'] for r in result['rows']} == {
        k:v for k,v in dict(fm=FM_DATE, fi=FI_DATE, q=REPORT_DATE).items() if k in runs}
    sql, params = shared_snapshot[0]
    assert 'max_coverage * 0.9' in sql and params['vigente'] is True
    assert params['currency'] is None


def test_different_fm_fi_dates_preserved_and_fx_uses_actual_valuation(shared_snapshot):
    snapshot = latest.snapshot('all', 'all')
    rates = Rates([dict(currency='USD', date=d, clp_per_unit=Decimal(v))
                   for d,v in [(FM_DATE,900), (FI_DATE,1000), (REPORT_DATE,800)]])
    d = analyze([], [], [], {MONTH:FI_DATE}, MONTH, 'CLP', 'all', None,
                'latest', FI_DATE, 'all', rates, {MONTH:snapshot})
    assert d['market_aum'] == 53000  # 10*900 + 20*1000 + 30*800
    assert d['nav_funds'] == 2 and d['eeff_funds'] == 1
    assert d['administrators'][0]['share'] == 100
    assert {f['run']:f['date'] for f in d['funds']} == dict(fm=FM_DATE,fi=FI_DATE,q=REPORT_DATE)
    assert {f['run']:f['aum'] for f in d['funds']} == dict(fm=9000,fi=20000,q=24000)


def test_native_currency_and_category_do_not_replace_reference_date(shared_snapshot):
    snapshot = latest.snapshot('all', 'all')
    snapshot['rows'][0]['currency'] = 'CLP'
    d = analyze([], [], [], {MONTH:FI_DATE}, MONTH, 'USD', 'all', 'Deuda',
                'latest', FI_DATE, 'all', snapshots={MONTH:snapshot})
    assert d['market_aum'] == 50 and d['reported_funds'] == 2
    assert d['date'] == FI_DATE
    empty = analyze([], [], [], {MONTH:FI_DATE}, MONTH, 'EUR', 'all', None,
                    'latest', FI_DATE, 'all', snapshots={MONTH:snapshot})
    assert empty['market_aum'] is None and empty['reported_funds'] == 0


def test_missing_fx_does_not_publish_partial_latest_ranking(shared_snapshot):
    snapshot = latest.snapshot('all', 'all')
    d = analyze([], [], [], {MONTH:FI_DATE}, MONTH, 'CLP', 'all', None,
                'latest', FI_DATE, 'all', Rates([]), {MONTH:snapshot})
    assert d['market_aum'] is None and d['funds'] == [] and d['administrators'] == []
    assert d['missing_fx_funds'] == 3
