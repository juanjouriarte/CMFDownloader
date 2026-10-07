from datetime import date
import importlib

import pytest

m = importlib.import_module('src.api.industry_workspace.portfolio_history')
D = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


def position(period=D[0], weight=4, **kwargs):
    return dict(periodo=period, weight=weight, source='cartera_naci', kind='fm', run='1',
                issuer_rut='12.345.678-9', issuer_name=None, issuer='Banco Uno', identifier='B1',
                instrument_type='B', currency_code='UF', country_code='CL', **kwargs)


def test_issuer_identity_uses_rut_and_separates_foreign_countries_and_unknowns():
    a = position()
    assert m.identity(a) == m.identity({**a, 'issuer_rut': '123456789', 'issuer': 'Nuevo nombre'})
    foreign = {**a, 'issuer_rut': None, 'issuer_name': ' Issuer   One '}
    assert m.identity(foreign) == m.identity({**foreign, 'issuer_name': 'ISSUER ONE'})
    assert m.identity(foreign)[0] != m.identity({**foreign, 'country_code': 'US'})[0]
    unknown = {**a, 'issuer_rut': None}
    assert not m.identity(unknown)[2]
    assert m.identity(unknown)[0] != m.identity({**unknown, 'identifier': 'B2'})[0]
    assert m.identity(a)[1] != m.identity({**a, 'currency_code': '$$'})[1]


def test_duplicate_rows_sum_without_filling_report_gaps_or_invalid_values():
    data = m.build_history([position(date(2026, 1, 1), 4), position(date(2026, 1, 1), 2),
                            position(D[2], float('nan'))], D, D)
    assert data['instruments'][0]['weights'] == [6, None, None]
    assert data['instruments'][0]['present'] == [True, False, True]
    assert data['coverage'][1]['reported_weight'] is None
    assert data['coverage'][2]['invalid_weights'] == 1
    assert data['coverage'][0]['report_dates'] == [date(2026, 1, 1)]


def test_exit_requires_observed_source_not_just_another_source():
    a = position()
    other = {**a, 'identifier': 'B2', 'periodo': D[1]}
    foreign = {**other, 'source': 'cartera_extr', 'periodo': D[2], 'identifier': 'F1'}
    result = m.build_history([a, other, foreign], D, D)
    holding = next(h for h in result['instruments'] if h['identifier'] == 'B1')
    assert holding['weights'] == [4, 0, None]
    assert holding['present'] == [True, False, False]


def test_partial_multi_source_position_stays_unknown():
    a = position()
    result = m.build_history([a, {**a, 'source': 'cartera_extr'}, {**a, 'periodo': D[1]}], D, D)
    assert result['instruments'][0]['weights'] == [8, None, None]


def test_currency_codes_do_not_confuse_usd_insurance_and_dollars():
    assert m.identity({**position(), 'currency_code': 'PROM'})[3] == 'USD'
    assert m.identity({**position(), 'currency_code': 'USD'})[3] == 'USD_SEGURO'


@pytest.mark.parametrize('kind,months,count,baseline', [
    ('fm', 12, 13, date(2025, 6, 30)), ('fi', 12, 5, date(2025, 6, 30)),
])
def test_loader_batches_dates_and_includes_opening_baseline(monkeypatch, kind, months, count, baseline):
    router = importlib.import_module('src.api.industry_workspace.router')
    calls = []
    def read(sql, params):
        calls.append((sql, params))
        return [{'period': date(2026, 6, 30)}] if len(calls) == 1 else []
    monkeypatch.setattr(router, 'rows', read)
    m.load_history.cache_clear()
    result = m.load_history(kind, '1', None, months, 0)
    assert len(result['dates']) == count
    assert result['dates'][0] == baseline
    assert len(calls) == 2
    assert calls[1][1]['start'] == baseline.replace(day=1)
    assert 'max(periodo)' in calls[1][0]  # One filing per source/month.
    assert 'run_fondo=:run' in calls[1][0]
    m.load_history.cache_clear()


def test_loader_rejects_unreported_period(monkeypatch):
    router = importlib.import_module('src.api.industry_workspace.router')
    monkeypatch.setattr(router, 'rows', lambda *args: [{'period': D[0]}])
    m.load_history.cache_clear()
    with pytest.raises(m.HTTPException) as exc:
        m.load_history('fm', '1', D[1], 12, 0)
    assert exc.value.status_code == 422
