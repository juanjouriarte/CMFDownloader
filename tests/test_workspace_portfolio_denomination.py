from datetime import date
import pytest
from src.api.industry_workspace.portfolio_denomination import denomination_weights
from src.api.industry_workspace.portfolio_history import build_history


def row(**kw):
    return dict(dict(instrument_type='BCU', quantity_unit='UF', currency_code='$$', weight=10), **kw)


def test_denomination_is_independent_of_settlement():
    assert denomination_weights([row()]) == {'UF':10}
    assert denomination_weights([row(quantity_unit=None)]) == {'UNKNOWN':10}
    assert denomination_weights([row(quantity_unit='CU')]) == {'UNKNOWN':10}
    assert denomination_weights([row(quantity_unit='USD')]) == {'USD_SEGURO':10}
    assert denomination_weights([row(quantity_unit='PROM')]) == {'USD':10}


@pytest.mark.parametrize('code',['CFI','CFM','CFME','ETFA','ETFB','ETFC'])
def test_fund_units_do_not_infer_underlying_currency(code):
    assert denomination_weights([row(instrument_type=code)]) == {'FUND_UNITS':10}


def test_duplicate_units_merge_only_after_mapping_and_reconcile():
    assert denomination_weights([row(),row(quantity_unit='CLF'),row(quantity_unit='$$')]) == {'CLP':10,'UF':20}
    assert denomination_weights([row(weight=0)]) == {'UF':0}
    for weight in [None,-1,float('nan'),float('inf'),101]:
        assert denomination_weights([row(weight=weight)]) is None
    assert denomination_weights([]) == {}
    assert denomination_weights([],observed=False) is None


def test_history_gaps_and_partial_sources_are_not_zero_denomination():
    ds=[date(2026,1,31),date(2026,2,28),date(2026,3,31)]
    r=dict(row(),periodo=ds[0],source='cartera_naci',identifier='BCU1',issuer_rut='12345')
    d=build_history([r,dict(r,periodo=ds[1],identifier='OTHER')],ds,ds)
    h=d['instruments'][0]
    assert h['denomination_weights']==[{'UF':10},{},None]
    d=build_history([r,dict(r,source='cartera_extr'),dict(r,periodo=ds[1])],ds,ds)
    assert d['instruments'][0]['denomination_weights']==[{'UF':20},None,None]
