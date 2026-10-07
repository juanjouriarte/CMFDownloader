from copy import deepcopy
import hashlib
import json
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from src.api import shared_rankings as m

CONFIG=dict(version=1,title='Equipo deuda',metric='returns',kind='all',currency='CLP',period='1M',
    group='fund',admin='',category='',fundKeys=['fm:1','fi:1'],series={'fm:1':['A',None],'fi:1':['B']},
    search='',direction='desc',sort='value',limit=0,range=None)


@pytest.mark.parametrize('change',[
    dict(title='  '),dict(kind='wrong'),dict(currency='USD+CLP'),dict(period='3M'),
    dict(fundKeys=['1']),dict(kind='fm'),dict(series={'fm:99':['A']}),
    dict(series={'fm:1':[1]}),dict(series={'fm:1':['A']*501}),dict(category='garbage'),
    dict(metric='nnm',range={'from':'2026-02-30','to':'2026-03-01'}),
    dict(metric='nnm',range={'from':'2026-03-02','to':'2026-03-01'}),
])
def test_bad_configuration_rejected(change):
    with pytest.raises(ValidationError):m.RankingConfig.model_validate({**CONFIG,**change})


def test_config_preserves_exact_series_and_date_range():
    assert m.RankingConfig.model_validate(CONFIG).model_dump(mode='json',by_alias=True)==CONFIG
    config={**CONFIG,'metric':'nnm','range':{'from':'2026-01-01','to':'2026-09-24'}}
    assert m.RankingConfig.model_validate(config).model_dump(mode='json',by_alias=True)==config


def test_complete_group_selections_can_exceed_100_funds_without_truncation():
    config={**CONFIG,'fundKeys':[f'fm:{i}' for i in range(2000)],'series':{'fm:1':['A']}}
    result=m.RankingConfig.model_validate(config)
    assert len(result.fundKeys)==2000
    assert result.series=={'fm:1':['A']}
    with pytest.raises(ValidationError):
        m.RankingConfig.model_validate({**config,'fundKeys':config['fundKeys']+['fm:2000']})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('RANKINGS_KEYS',json.dumps({name:hashlib.sha256(key.encode()).hexdigest() for name,key in [('Editor A','test-a'),('Editor B','test-b')]}))
    monkeypatch.setenv('RANKINGS_ACCESS','private')
    app=FastAPI();app.include_router(m.router)
    return TestClient(app)


def test_all_routes_require_private_access(client):
    id=str(uuid4())
    for method,path,body in [('get','',None),('get','/'+id,None),('post','',dict(id=id,config=CONFIG)),
                            ('put','/'+id,dict(config=CONFIG,expected_version=1)),('delete','/'+id+'?expected_version=1',None)]:
        response=client.request(method,'/shared-rankings'+path,json=body)
        assert response.status_code==401
        assert response.headers['cache-control']=='no-store'
    result=client.get('/shared-rankings/session',headers={'Authorization':'Bearer test-a'})
    assert result.json()==dict(user='Editor A')


def test_invalid_auth_config_fails_closed(client,monkeypatch):
    monkeypatch.setenv('RANKINGS_KEYS','invalid')
    assert client.get('/shared-rankings',headers={'Authorization':'Bearer test-a'}).status_code==503


@pytest.mark.skipif(os.getenv('RUN_WORKSPACE_DB_TESTS')!='1',reason='requires migrated local DB')
def test_team_crud_conflicts_audit_and_persistence(client,monkeypatch):
    from src.db.engine import engine
    a={'Authorization':'Bearer test-a'};b={'Authorization':'Bearer test-b'}
    with engine.connect() as conn:
        txn=conn.begin()
        monkeypatch.setattr(m,'SessionLocal',sessionmaker(bind=conn,join_transaction_mode='create_savepoint'))
        try:
            id=str(uuid4());payload=dict(id=id,config=deepcopy(CONFIG))
            created=client.post('/shared-rankings',headers=a,json=payload)
            assert created.status_code==201,created.text
            assert created.json()['created_by']=='Editor A'
            assert created.json()['version']==1
            assert client.post('/shared-rankings',headers=a,json=payload).json()['version']==1
            assert client.get('/shared-rankings/'+id,headers=b).json()['config']==CONFIG
            listing=client.get('/shared-rankings',headers=b).json()
            assert id in [row['id'] for row in listing['rows']]
            changed={**CONFIG,'title':'Actualizado por colega'}
            updated=client.put('/shared-rankings/'+id,headers=b,json=dict(config=changed,expected_version=1))
            assert updated.status_code==200 and updated.json()['version']==2
            assert updated.json()['updated_by']=='Editor B'
            assert client.put('/shared-rankings/'+id,headers=a,json=dict(config=CONFIG,expected_version=1)).status_code==409
            assert client.delete('/shared-rankings/'+id+'?expected_version=1',headers=a).status_code==409
            assert client.get('/shared-rankings/'+id,headers=a).json()['config']==changed
            assert client.delete('/shared-rankings/'+id+'?expected_version=2',headers=b).status_code==200
            assert client.get('/shared-rankings/'+id,headers=a).status_code==404
            history=conn.execute(text('SELECT action,actor FROM shared_ranking_revisions WHERE ranking_id=:id ORDER BY version'),dict(id=id)).all()
            assert history==[('create','Editor A'),('update','Editor B'),('delete','Editor B')]
        finally:txn.rollback()
