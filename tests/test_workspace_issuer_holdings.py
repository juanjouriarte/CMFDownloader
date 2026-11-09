import json
import pytest
from fastapi import HTTPException
from src.api.industry_workspace.issuer_holdings import issuer_filter, group_holders


def test_identity_uses_rut_or_country_and_exact_name():
    sql, params = issuer_filter('Display name', json.dumps(['rut', '89.912.300']))
    assert 'issuer_rut' in sql and params == {'rut': '89912300'}
    sql, params = issuer_filter('Display name', json.dumps(['name', 'US', 'Some  issuer']))
    assert 'country_code' in sql and params == {'country': 'US', 'name': 'SOME ISSUER'}
    assert issuer_filter('Exact display') == ('issuer=:issuer', {'issuer': 'Exact display'})


@pytest.mark.parametrize('identity', ['{}', 'null', 'bad', '["unknown","ABC"]', '["rut", ""]'])
def test_rejects_ambiguous_identity(identity):
    with pytest.raises(HTTPException):
        issuer_filter('Unknown', identity)


def test_groups_all_positions_without_mixing_funds_or_report_dates():
    base = dict(kind='fm', run='1', periodo='2026-09-30', fund_name='Fund', admin='AGF')
    positions = [dict(base, identifier=str(i), weight=0.1) for i in range(105)]
    positions += [dict(base, kind='fi', weight=5), dict(base, periodo='2026-08-31', weight=2),
                  dict(base, run='2', weight=None), dict(base, run='2', weight=3)]
    holders = group_holders(positions)
    assert len(holders) == 4
    assert holders[0]['weight'] == pytest.approx(10.5)
    assert len(holders[0]['positions']) == 105
    assert holders[-1]['weight'] is None
    assert holders[-1]['invalid_weights'] == 1
