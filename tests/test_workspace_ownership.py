from datetime import date
from src.api.industry_workspace.ownership import positions, changes, directory, concentration


def row(rut='1', run='f', value=10, **kwargs):
    return dict(rut=rut, run=run, weight=value, rank=1, name='Investor '+str(rut),
                admin='BTG Pactual Chile AGF', fund_name='Fund '+run, person_type='A', **kwargs)


def test_duplicate_holder_percentages_and_unknowns_are_preserved():
    assert positions([row(value=5),row(value=3)])[0]['weight'] == 8
    assert positions([row(value=5),row(value=None)])[0]['weight'] is None
    assert positions([row(value=60),row(value=60)])[0]['weight'] is None
    assert positions([row(value=0)])[0]['weight'] == 0
    assert positions([row()])[0]['person_type'] == 'J'


def test_movements_do_not_infer_sales_or_zeros_from_absent_disclosures():
    old = positions([row(),row('2'),row('3',run='missing'),row('4',value=None)])
    new = positions([row(value=14),row('new',value=4),row('4',value=3)])
    result={r['rut']:r for r in changes(new,old)}
    assert result['1']['delta']==4
    assert result['1']['state']=='increased'
    assert result['2']['state']=='unlisted' and result['2']['after'] is None and result['2']['delta'] is None
    assert result['3']['state']=='no_report'
    assert result['new']['state']=='appears' and result['new']['before'] is None
    assert result['4']['state']=='unknown'


def test_directory_counts_distinct_funds_and_does_not_add_percentages():
    rows=positions([row(value=90),row(run='g',value=80),row(None,run='h')])
    result=directory(rows)
    assert len(result)==1
    assert result[0]['funds']==2 and result[0]['max_weight']==90 and result[0]['btg_funds']==2


def test_concentration_includes_unidentified_holders_and_rejects_incomplete_weights():
    rows=positions([row(value=60),row('2',value=20),row(None,value=10)])
    item=concentration(rows)[0]
    assert item['top1']==60 and item['top5']==90 and item['holders']==3
    assert concentration(positions([row(value=60),row('2',value=60)]))[0]['top1'] is None
    assert concentration(positions([row(value=60),row('2',value=None)]))[0]['top5'] is None


def test_endpoint_filters_preserve_filing_presence_and_global_btg_relationship(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.industry_workspace import ownership as module
    current_date, previous_date = date(2026,6,30), date(2026,3,31)
    current = positions([row(value=20),{**row(run='other',value=30),'admin':'Other AGF'}, {**row('n',value=1),'person_type':'N'}])
    previous = positions([row('old',value=20),{**row('n',value=2),'person_type':'N'}])
    monkeypatch.setattr(module, 'selected_period', lambda p: (p or current_date,[current_date,previous_date],0))
    monkeypatch.setattr(module, 'snapshot', lambda period,bucket: current if period==current_date else previous)
    monkeypatch.setattr(module, 'nav_snapshot', lambda period,bucket,age: {})
    app=FastAPI();app.include_router(module.router);client=TestClient(app)
    directory_response=client.get('/ownership',params={'admin':'Other AGF','btg':'with','search':'Investor 1'}).json()
    assert directory_response['total']==1
    assert directory_response['rows'][0]['funds']==1 and directory_response['rows'][0]['btg_funds']==1
    movements=client.get('/ownership',params={'view':'movements','person':'J'}).json()
    assert next(r for r in movements['rows'] if r['rut']=='old')['state']=='unlisted'
    assert next(r for r in movements['rows'] if r['rut']=='1' and r['run']=='f')['state']=='appears'
    assert client.get('/ownership',params={'limit':1,'offset':1}).json()['total']==2
    assert client.get('/ownership',params={'limit':201}).status_code==422
    assert client.get('/ownership',params={'view':'invalid'}).status_code==422


def test_owner_valuation_separates_currencies_and_preserves_zero_and_partial_coverage():
    from src.api.industry_workspace.ownership import value_positions, sort_directory
    records=positions([row(run='clp',value=10),row(run='usd',value=50),row(run='missing',value=30),row(run='zero',value=0),row(run='invalid',value=None)])
    navs={r:dict(aum=a,currency=c,date=date(2026,6,30)) for r,a,c in [('clp',1000,'CLP'),('usd',2000,'USD'),('zero',500,'CLP'),('invalid',100,'CLP')]}
    result=value_positions(records,navs,'CLP')
    assert result['aum']==100 and result['valued_funds']==2
    assert result['unvalued_funds']==2 and result['other_currency_funds']==1
    assert value_positions(records,navs,'EUR')['aum'] is None
    assert value_positions([records[3]],navs,'CLP')['aum']==0
    data=[dict(rut=str(i),name=str(i),aum=a) for i,a in enumerate([None,100,0,400])]
    assert [r['aum'] for r in sort_directory(data,'aum','desc')]==[400,100,0,None]
    assert [r['aum'] for r in sort_directory(data,'aum','asc')]==[0,100,400,None]


def test_aum_ordering_precedes_pagination(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.industry_workspace import ownership as module
    period=date(2026,6,30)
    current=positions([row('a',value=10),row('b',value=80),row('c',run='no_nav',value=100)])
    monkeypatch.setattr(module,'selected_period',lambda p:(period,[period],0))
    monkeypatch.setattr(module,'snapshot',lambda p,b:current if p==period else [])
    monkeypatch.setattr(module,'nav_snapshot',lambda p,b,age:{'f':dict(aum=1000,currency='CLP',date=period)})
    app=FastAPI();app.include_router(module.router);client=TestClient(app)
    assert client.get('/ownership?nav_days=7').status_code==200
    assert client.get('/ownership?nav_days=31').status_code==200
    assert client.get('/ownership?nav_days=8').status_code==422
    assert client.get('/ownership?limit=1').json()['rows'][0]['rut']=='b'
    assert client.get('/ownership?limit=1&offset=1').json()['rows'][0]['rut']=='a'
    assert client.get('/ownership?limit=1&direction=asc').json()['rows'][0]['rut']=='a'
    assert client.get('/ownership?currency=USD').json()['rows'][0]['aum'] is None
