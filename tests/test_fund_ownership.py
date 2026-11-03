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
