from datetime import date
import importlib

m = importlib.import_module('src.api.industry_workspace.instrument_history')
D = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
AXES = {'fm': D, 'fi': [date(2025, 12, 31), D[-1]]}


def position(period=D[0], run='1', source='cartera_naci', **extra):
    return dict(kind='fm', run=run, source=source, periodo=period, identifier='BOND',
                fund_name='Fund '+run, admin='AGF', currency_code='UF', weight=4,
                quantity=100, quantity_unit='UF') | extra


def report(period=D[0], run='1', source='cartera_naci', kind='fm'):
    return dict(kind=kind, run=run, source=source, periodo=period)


def test_historical_holders_survive_exit_and_missing_reports_remain_null():
    groups = m.build_groups([position(), position(D[1], run='2')],
                            [report(), report(D[1]), report(D[1], run='2'), report(D[2], run='2')], AXES)
    by_run = {h['run']: h for h in groups[0]['rows']}
    assert by_run['1']['quantities'] == [100, 0, None]
    assert by_run['1']['weights'] == [4, 0, None]
    assert by_run['2']['quantities'] == [None, 100, 0]
    assert by_run['1']['report_dates'] == [[D[0]], [D[1]], []]
    assert by_run['1']['present'] == [True, False, False]


def test_duplicate_units_and_partial_sources_do_not_create_false_changes():
    positions = [position(), position(source='cartera_extr'), position(D[1]),
                 position(D[2], quantity_unit='$$'), position(D[2], quantity_unit='UF')]
    reports = [report(), report(source='cartera_extr'), report(D[1]),
               report(D[2]), report(D[2], source='cartera_extr')]
    h = m.build_groups(positions, reports, AXES)[0]['rows'][0]
    assert h['weights'] == [8, None, 8]
    assert h['quantities'] == [200, None, None]
    assert h['quantity_units'] == ['UF', 'UF', None]


def test_quantity_validity_is_independent_of_weights_and_units_are_preserved():
    positions = [position(weight=None), position(D[1], quantity_unit='$$'),
                 position(D[2], quantity=float('nan'))]
    h = m.build_groups(positions, [report(d) for d in D], AXES)[0]['rows'][0]
    assert h['weights'] == [None, 4, 4]
    assert h['quantities'] == [100, 100, None]
    assert h['quantity_units'] == ['UF', '$$', 'UF']


def test_latest_source_filing_wins_without_synthesizing_quarterly_months():
    stale = position(date(2026, 1, 1))
    latest = position(date(2026, 1, 25), quantity=70)
    fi = position(D[2], kind='fi', source='cartera_fi_nac')
    groups = m.build_groups([stale, latest, fi],
                            [report(date(2026, 1, 25)), report(D[2], source='cartera_fi_nac', kind='fi')], AXES)
    assert groups[0]['rows'][0]['quantities'] == [70, None, None]
    assert groups[1]['dates'] == [date(2025, 12, 31), D[-1]]
    assert groups[1]['rows'][0]['quantities'] == [None, 100]
    assert not m.build_groups([stale], [report(date(2026, 1, 25))], AXES)[0]['rows']


def test_currencies_are_separate_rows_and_fund_kinds_do_not_collide():
    positions = [position(), position(currency_code='PROM'),
                 position(D[2], kind='fi', source='cartera_fi_nac')]
    groups = m.build_groups(positions, [report(), report(D[2], source='cartera_fi_nac', kind='fi')], AXES)
    assert len(groups[0]['rows']) == 2
    assert {h['currency'] for h in groups[0]['rows']} == {'UF', 'USD'}
    assert len({h['id'] for g in groups for h in g['rows']}) == 3


def test_loader_batches_all_holders_in_three_queries_and_binds_identity(monkeypatch):
    router = importlib.import_module('src.api.industry_workspace.router')
    calls = []
    def read(sql, params):
        calls.append((sql, dict(params)))
        return ([{'kind': 'fm', 'periodo': D[-1]}, {'kind': 'fi', 'periodo': D[-1]}] if len(calls) == 1
                else [position(D[-1]), position(D[-1], run='2')] if len(calls) == 2
                else [report(D[-1]), report(D[-1], run='2')])
    monkeypatch.setattr(router, 'rows', read)
    m.load_history.cache_clear()
    result = m.load_history("BOND'", None, "Issuer'", 12, 0)
    assert len(calls) == 3
    assert len(result['groups'][0]['dates']) == 13
    assert len(result['groups'][1]['dates']) == 5
    assert calls[1][1]['identifier'] == "BOND'"
    assert "Issuer'" not in calls[1][0]
    assert 'issuer=:issuer' in calls[1][0]
    assert 'IS NOT DISTINCT FROM :instrument_type' in calls[1][0]
    assert calls[2][1]['cartera_naci_runs'] == ['1', '2']
    assert 'run_fondo=ANY(:cartera_naci_runs)' in calls[2][0]
    m.load_history.cache_clear()
