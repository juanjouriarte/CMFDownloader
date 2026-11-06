from datetime import date
from src.api.industry_workspace.fund_ownership import ownership_history


def row(month,rut='1',w=20,rank=1,name='Owner'):
    return dict(periodo=date(2026,month,30 if month in (6,9) else 31),rut=rut,weight=w,rank=rank,name=name)


def test_missing_quarter_and_disappearing_holder_are_not_zero():
    result=ownership_history([row(3),row(6,'2',10),row(12,w=30)])
    assert result['dates']==[date(2026,3,31),date(2026,6,30),date(2026,9,30),date(2026,12,31)]
    owner=next(r for r in result['rows'] if r['rut']=='1')
    assert owner['weights']==[20,None,None,30]
    assert owner['states']==['reported','not_listed','no_report','reported']
    assert owner['changes']==[None]*4


def test_stable_identity_duplicate_rows_zero_and_name_changes():
    result=ownership_history([row(3,w=10),row(3,w=5,rank=2),row(6,w=0,name='New name'),row(9,w=12)])
    owner=result['rows'][0]
    assert owner['weights']==[15,0,12]
    assert owner['changes']==[None,-15,12]
    assert owner['name']=='Owner'


def test_unknown_identity_is_not_stitched_by_name_or_rank():
    result=ownership_history([row(3,None),row(6,None)])
    assert len(result['rows'])==2
    assert all(r['changes']==[None,None] for r in result['rows'])


def test_invalid_filing_and_missing_weights_block_changes():
    result=ownership_history([row(3,w=70),row(3,'2',40,2),row(6,w=20),row(9,w=None)])
    owner=next(r for r in result['rows'] if r['rut']=='1')
    assert result['coverage'][0]['invalid_total']
    assert owner['weights']==[None,20,None]
    assert owner['states']==['invalid_report','reported','missing_weight']
    assert owner['changes']==[None,None,None]
    assert ownership_history([])==dict(dates=[],rows=[],coverage=[])


def test_rut_aliases_link_but_zero_and_foreign_placeholders_do_not():
    result=ownership_history([row(3,'01.111.111-K',10),row(6,'1111111',12),row(3,'0',5,2),row(6,'0',6,2)])
    linked=next(r for r in result['rows'] if r['rut']=='1111111')
    assert linked['changes']==[None,2]
    assert len(result['rows'])==3


def test_comparer_defaults_to_latest_common_quarter_without_mixing_dates():
    from src.api.industry_workspace.fund_ownership import comparison_period
    funds=[dict(run='1'),dict(run='2')]
    available=[dict(run='1',periodo=date(2026,6,30)),dict(run='1',periodo=date(2026,9,30)),dict(run='2',periodo=date(2026,6,30))]
    assert comparison_period(funds,available)[0]==date(2026,6,30)
    assert comparison_period(funds,available,date(2026,9,30))[0]==date(2026,9,30)
    assert comparison_period(funds,[])[0] is None


def test_fund_matrix_preserves_shared_ruts_unknowns_and_missing_fund_reports():
    from src.api.industry_workspace.fund_ownership import ownership_comparison
    funds=[dict(run='1',name='One'),dict(run='2',name='Two'),dict(run='3',name='Missing')]
    records=[dict(row(3,'111',20),run='1'),dict(row(6,'111',25),run='1'),
             dict(row(6,'111',15),run='2'),dict(row(6,None,5,2),run='1'),dict(row(6,None,7,2),run='2')]
    result=ownership_comparison(funds,records,date(2026,6,30))
    assert len(result['rows'])==3
    common=next(r for r in result['rows'] if r['rut']=='111')
    assert common['funds']==2
    assert [c['weight'] for c in common['cells']]==[25,15,None]
    assert [c['change'] for c in common['cells']]==[5,None,None]
    assert common['cells'][2]['state']=='no_report'
    assert all(r['funds']==1 for r in result['rows'] if r['rut'] is None)
    assert result['funds'][2]['reported'] is False


def test_matrix_does_not_turn_an_unlisted_holder_into_zero_or_count_it_as_shared():
    from src.api.industry_workspace.fund_ownership import ownership_comparison
    funds=[dict(run='1'),dict(run='2')]
    records=[dict(row(3,'111',20),run='1'),dict(row(6,'222',30),run='1'),dict(row(6,'111',0),run='2')]
    result=ownership_comparison(funds,records,date(2026,6,30))
    owner=next(r for r in result['rows'] if r['rut']=='111')
    assert owner['funds']==1 and owner['cells'][0]['state']=='not_listed'
    assert owner['cells'][0]['weight'] is None and owner['cells'][0]['change'] is None
    assert owner['cells'][1]['weight']==0


def test_comparer_has_no_common_date_and_invalid_filing_is_not_valued():
    from src.api.industry_workspace.fund_ownership import comparison_period, ownership_comparison
    funds=[dict(run='1'),dict(run='2')]
    available=[dict(run='1',periodo=date(2026,3,31)),dict(run='2',periodo=date(2026,6,30))]
    selected,_,common=comparison_period(funds,available)
    assert selected==date(2026,6,30) and common==[]
    records=[dict(row(6,'111',70),run='1'),dict(row(6,'222',50,2),run='1'),dict(row(6,'111',10),run='2')]
    result=ownership_comparison(funds,records,selected)
    assert result['funds'][0]['invalid_total']
    owner=next(r for r in result['rows'] if r['rut']=='111')
    assert owner['cells'][0]['weight'] is None and owner['cells'][0]['state']=='invalid_report'
    assert owner['cells'][1]['weight']==10


def test_comparer_rejects_malformed_runs_and_nonquarter_dates():
    import pytest
    from fastapi import HTTPException
    from src.api.industry_workspace.fund_ownership import compare_fund_ownership
    for runs,period in [('',None),('123,abc',None),('123',date(2026,1,31)),('123',date(2026,6,29))]:
        with pytest.raises(HTTPException) as exc:compare_fund_ownership(runs,period)
        assert exc.value.status_code==422
