from datetime import date
import importlib.util
import pytest

m=importlib.import_module('src.api.industry_workspace.router')


def test_capital_activity_uses_reported_units_and_exact_positive_bases():
    current = dict(cuotas_emitidas=150, cuotas_pagadas=120, num_cuotas_promesa=40, cuotas_suscritas_no_pagadas=30, valor_libro=999)
    previous = dict(cuotas_emitidas=100, cuotas_pagadas=100, num_cuotas_promesa=50)
    result = m.capital_changes(current, previous)
    assert result['issued_delta'] == 50 and result['issued_pct'] == 50
    assert result['paid_delta'] == 20 and result['paid_pct'] == 20
    assert result['promised_delta'] == -10 and result['promised_pct'] == -20
    assert result['pending_pct'] == 20
    assert 'capital_called_clp' not in result


@pytest.mark.parametrize('base', [None, 0, -10, float('nan')])
def test_capital_activity_does_not_invent_growth_without_a_positive_base(base):
    result = m.capital_changes(dict(cuotas_emitidas=10), dict(cuotas_emitidas=base))
    assert result['issued_pct'] is None
    assert result['issued_delta'] == (10 if base == 0 else None)


def test_capital_activity_missing_previous_quarter_is_not_zero():
    result = m.capital_changes(dict(cuotas_pagadas=50, cuotas_suscritas_no_pagadas=10), None)
    assert result['paid_delta'] is None and result['paid_pct'] is None
    assert result['has_previous'] is False
    assert result['pending_pct'] == pytest.approx(100/6)


@pytest.mark.parametrize('paid,promised,unpaid,issued,signal', [
    (120, 30, 50, 100, 'commitment_funding'),
    (120, 50, 30, 100, 'commitment_funding'),
    (120, 50, 50, 100, 'paid_increase'),
    (100, 30, 30, 150, 'issuance_increase'),
    (90, 30, 30, 100, 'none'),
    (120, None, None, 150, 'paid_increase'),
])
def test_capital_signals_require_paid_growth_for_possible_funding(paid, promised, unpaid, issued, signal):
    result = m.capital_changes(dict(cuotas_pagadas=paid, num_cuotas_promesa=promised,
        cuotas_suscritas_no_pagadas=unpaid, cuotas_emitidas=issued),
        dict(cuotas_pagadas=100, num_cuotas_promesa=50, cuotas_suscritas_no_pagadas=50, cuotas_emitidas=100))
    assert result['signal'] == signal
    assert result['possible_call'] == (signal == 'commitment_funding')
    assert result['issuance_increase'] == (issued > 100)
    assert result['confirmed_call'] is False


def test_radar_excludes_rescatable_unknown_and_non_nav_funds(monkeypatch):
    current=date(2026,6,30);previous=date(2026,3,31)
    def query(sql, params=None):
        if 'SELECT DISTINCT periodo' in sql:
            return [dict(periodo=current)]
        if 'FROM fondos_inversion f' in sql:
            assert 'f.rescatable IS FALSE' in sql
            return [dict(run_fondo='closed',categoria='FI_DEUDA_PRIVADA',grupo='Capital Privado'),
                    dict(run_fondo='no_nav',categoria='FI_INMOB_RENTA',grupo='Inmobiliario')]
        return [dict(run_fondo=run,periodo=p,cuotas_pagadas=100 if p==previous else 120)
                for run in ['closed','open','unknown','no_nav'] for p in [previous,current]]
    monkeypatch.setattr(m,'rows',query)
    monkeypatch.setattr(m,'funds',lambda:[dict(kind='fi',run=run,currency='CLP',admin='AGF',
        category='Deuda Privada',name=run,data_date=current) for run in ['closed','open','unknown']])
    result=m.capital_activity(currency='CLP')
    assert [r['run'] for r in result['rows']] == ['closed']
    assert result['eligible']==1 and result['rows'][0]['rescatable'] is False
    assert m.capital_activity(currency='CLP',strategy='real_estate')['rows']==[]
    assert len(m.capital_activity(currency='CLP',strategy='private_debt')['rows'])==1

def nav(run,admin,aum,day,kind='fm',month=date(2026,8,1),category='Deuda',nnm=0):
    return dict(run_fondo=run,admin=admin,aum=aum,latest_data_date=day,kind=kind,month=month,category=category,nnm=nnm,aportes=max(nnm,0),rescates=max(-nnm,0))

def test_market_denominator_date_and_share_sum():
    d=date(2026,8,31)
    result=m.market_snapshots([nav('a','BTG',60,d),nav('b','Other',40,d),nav('c','Other',900,date(2026,8,30))])[0]
    assert result['date']==d
    assert result['market_aum']==100
    assert result['coverage_pct']==pytest.approx(200/3)
    assert sum(a['share'] for a in result['administrators'])==pytest.approx(100)
    assert result['administrators'][0]['share']==60

def test_reference_prefers_joint_fund_type_date():
    d=date(2026,8,31);earlier=date(2026,8,28)
    result=m.market_snapshots([nav('a','BTG',10,d),nav('b','Other',10,d),nav('c','Other',10,earlier),nav('d','BTG',10,earlier,'fi')])[0]
    assert result['date']==earlier
    assert result['reported_funds']==2

def test_concentration_weights_and_zero_aum():
    d=date(2026,8,31)
    result=m.market_snapshots([nav('a','BTG',50,d),nav('b','BTG',50,d),nav('c','Empty',0,d)])[0]
    assert result['administrators'][0]['hhi']==5000
    assert result['administrators'][0]['top5_pct']==100
    assert result['administrators'][1]['hhi'] is None

def test_flows_migrations_baseline_and_no_percentage_without_base():
    d=date(2026,7,31);start=date(2026,8,1)
    data=[nav('a','BTG',100,d,month=date(2026,7,1)),nav('a','BTG',120,date(2026,8,31),nnm=30),nav('b','New',10,date(2026,8,31),nnm=10)]
    result=m.aggregate_flows(data,{('a',start):10},start,start,'admin',d)
    btg=next(r for r in result if r['name']=='BTG');new=next(r for r in result if r['name']=='New')
    assert btg['net']==20 and btg['flow_pct']==20
    assert new['flow_pct'] is None
    assert sum(r['net'] for r in result)==sum(r['reported']-r['migrations'] for r in result)

def test_ytd_sums_all_months_but_counts_funds_once():
    start=date(2026,1,1);end=date(2026,2,1);base=date(2025,12,31)
    data=[nav('a','BTG',100,base,month=date(2025,12,1)),nav('a','BTG',110,date(2026,1,31),month=start,nnm=10),nav('a','BTG',105,date(2026,2,28),month=end,nnm=-5)]
    r=m.aggregate_flows(data,{},start,end,'category',base)[0]
    assert r['net']==5 and r['funds']==1 and r['flow_pct']==5

def position(identifier,weight,issuer='Issuer',kind='BOND'):
    return dict(source='naci',identifier=identifier,weight=weight,issuer=issuer,issuer_rut='123',instrument_type=kind,instrument_name=kind,country='Chile')

def test_diff_new_exit_change_and_missing_weight():
    old=[position('A',10),position('B',5),position('C',None),position(None,20)]
    new=[position('A',12),position('D',7),position('C',6),position(None,30)]
    r={p['identifier']:p for p in m.portfolio_diff(old,new)}
    assert r['A']['delta']==2 and r['A']['status']=='increased'
    assert r['B']['delta']==-5 and r['B']['status']=='exited'
    assert r['D']['delta']==7 and r['D']['status']=='new'
    assert r['C']['delta'] is None and r['C']['status']=='unknown'
    assert None not in r

def test_duplicate_lines_aggregate_without_hiding_unknown_weights():
    r=m.portfolio_diff([position('A',3),position('A',2)],[position('A',4),position('A',2)])
    assert len(r)==1 and r[0]['delta']==1
    r=m.portfolio_diff([position('A',3),position('A',None)],[position('A',4)])
    assert r[0]['before'] is None

def test_partial_coverage_concentration_is_normalized_and_country_grouped():
    r=m.concentration([position('A',20,'One'),position('B',20,'Two'),position('C',None),position('D',-5)],'issuer')
    assert r['covered_weight']==40
    assert r['hhi_covered']==5000 and r['top5_pct_covered']==100
    assert sum(b['share_covered'] for b in r['buckets'])==100


def test_source_fractional_weights_are_not_discarded():
    import re
    sql=m.positions_sql(run='8057',kind='fm',period=date(2026,8,1))
    pattern=re.search(r"~ '([^']+)'",sql).group(1)
    assert re.fullmatch(pattern,'.507')
    assert re.fullmatch(pattern,'0.507')
    assert re.fullmatch(pattern,'-0.1')
    assert re.fullmatch(pattern,'5.07e-1')
    assert not re.fullmatch(pattern,'NaN')
    assert not re.fullmatch(pattern,'unknown')
