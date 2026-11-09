import hashlib
import json
import os
from datetime import date
from importlib import import_module
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

m=import_module('src.api.classification_admin')


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('CLASSIFICATION_ADMIN_KEYS',json.dumps({'test-editor':hashlib.sha256(b'test-key').hexdigest(),'other-editor':hashlib.sha256(b'other-key').hexdigest()}))
    app=FastAPI();app.include_router(m.router)
    return TestClient(app)


def test_admin_routes_fail_closed_and_resolve_actor_server_side(client,monkeypatch):
    for path in ['/session','/catalog?kind=fi','/funds','/funds/fi/123']:
        assert client.get('/classification-admin'+path).status_code==401
    assert client.put('/classification-admin/funds/fi/123',json={'action':'lock','code':'FI_PE','group':'Capital Privado','reason':'Valid reason','expected_version':0}).status_code==401
    assert client.get('/classification-admin/session',headers={'Authorization':'Bearer incorrect'}).status_code==401
    authenticated=client.get('/classification-admin/session',headers={'Authorization':'Bearer test-key'})
    assert authenticated.json()=={'user':'test-editor'}
    assert client.get('/classification-admin/session',headers={'Authorization':'Bearer other-key'}).json()=={'user':'other-editor'}
    assert authenticated.headers['cache-control']=='no-store'
    monkeypatch.delenv('CLASSIFICATION_ADMIN_KEYS')
    assert client.get('/classification-admin/session',headers={'Authorization':'Bearer test-key'}).status_code==503
    monkeypatch.setenv('CLASSIFICATION_ADMIN_KEYS','malformed')
    assert client.get('/classification-admin/session',headers={'Authorization':'Bearer test-key'}).status_code==503


def test_catalog_and_input_contract(client,monkeypatch):
    monkeypatch.setattr(m,'all_category_options',lambda session,kind:m.category_options(kind))
    auth={'Authorization':'Bearer test-key'}
    fi=client.get('/classification-admin/catalog?kind=fi',headers=auth).json()
    fm=client.get('/classification-admin/catalog?kind=fm',headers=auth).json()
    assert any(c['code']=='FI_PE' for c in fi) and any(c['code']=='RF<90NAC' for c in fm)
    assert not any(c['code'].startswith('FI_') for c in fm)
    for payload in [dict(action='lock',reason='short',expected_version=0),
                    dict(action='lock',reason='        ',expected_version=0),
                    dict(action='unlock',reason='Valid reason',expected_version=0,actor='spoofed')]:
        assert client.put('/classification-admin/funds/fi/123',headers=auth,json=payload).status_code==422
    assert client.get('/classification-admin/catalog?kind=invalid',headers=auth).status_code==422


def test_new_category_requires_editor_and_valid_fields(client):
    payload=dict(tipo='Deuda',grupo='Privada',name='Nueva estrategia',reason='Reviewed fund strategy')
    assert client.post('/classification-admin/catalog?kind=fi',json=payload).status_code==401
    for invalid in [dict(payload,tipo='  '),dict(payload,reason='short'),dict(payload,name='x'*161),dict(payload,actor='spoofed')]:
        assert client.post('/classification-admin/catalog?kind=fi',headers={'Authorization':'Bearer test-key'},json=invalid).status_code==422


@pytest.mark.parametrize('kind',['fm','fi'])
def test_custom_category_creation_assignment_and_duplicate_protection(transaction,kind):
    session=transaction
    new=m.NewCategory(tipo='Deuda',grupo='  Grupo   prueba  ',name='Subcategoría de prueba',reason='Taxonomy integration test')
    category=m.create_category(session,kind,new,'test-editor')
    assert category['grupo']=='Grupo prueba'
    assert category in m.all_category_options(session,kind)
    assert not any(c['code']==category['code'] for c in m.all_category_options(session,'fm' if kind=='fi' else 'fi'))
    duplicate=m.NewCategory(tipo='deuda',grupo='GRUPO PRUEBA',name='subcategoria de prueba',reason='Duplicate test reason')
    with pytest.raises(HTTPException) as error:m.create_category(session,kind,duplicate,'other-editor')
    assert error.value.status_code==409
    builtin=m.category_options(kind)[0]
    with pytest.raises(HTTPException) as error:m.create_category(session,kind,m.NewCategory(**{k:builtin[k] for k in ['tipo','grupo','name']},reason='Existing category test'),'test-editor')
    assert error.value.status_code==409
    run=session.execute(text(f'SELECT run_fondo FROM categoria_{kind} ORDER BY run_fondo LIMIT 1')).scalar_one()
    before=m.fetch_fund(session,kind,run)
    saved=m.apply_edit(session,kind,run,m.Edit(action='lock',code=category['code'],group=category['grupo'],reason='Apply new custom category',expected_version=before['version']),'test-editor')
    assert saved['locked'] and saved['applied']==category
    assert session.execute(text(f'SELECT categoria FROM categoria_{kind}_effective WHERE run_fondo=:run ORDER BY periodo DESC LIMIT 1'),dict(run=run)).scalar_one()==category['code']
    row=session.execute(text('SELECT created_by,reason FROM classification_catalog_custom WHERE code=:code'),category).mappings().one()
    assert row['created_by']=='test-editor' and row['reason']=='Taxonomy integration test'


def test_category_creation_http_persists_and_public_catalog_exposes_empty_categories(client,transaction,monkeypatch):
    from sqlalchemy.orm import Session
    categories=import_module('src.api.categories')
    monkeypatch.setattr(m,'SessionLocal',lambda:Session(bind=transaction.connection(),join_transaction_mode='create_savepoint'))
    monkeypatch.setattr(categories,'SessionLocal',m.SessionLocal)
    auth={'Authorization':'Bearer test-key'}
    payload=dict(tipo='Deuda',grupo='Grupo HTTP prueba',name='Subcategoría HTTP prueba',reason='Create through authenticated API')
    result=client.post('/classification-admin/catalog?kind=fi',headers=auth,json=payload)
    assert result.status_code==201 and result.headers['cache-control']=='no-store'
    assert result.json() in client.get('/classification-admin/catalog?kind=fi',headers=auth).json()
    public=categories._catalog_for('categoria_fi_effective')
    leaf=next(c for t in public for g in t['groups'] for c in g['categories'] if c['code']==result.json()['code'])
    assert leaf['fund_count']==0


@pytest.fixture
def transaction():
    if os.getenv('RUN_WORKSPACE_DB_TESTS')!='1':pytest.skip('Enable local transactional PostgreSQL checks')
    from src.db.engine import engine
    from sqlalchemy.orm import Session
    connection=engine.connect();outer=connection.begin()
    session=Session(bind=connection,join_transaction_mode='create_savepoint')
    try:yield session
    finally:session.close();outer.rollback();connection.close()


@pytest.mark.parametrize('kind',['fm','fi'])
def test_override_survives_classifier_update_and_unlocks_to_latest_output(transaction,kind):
    session=transaction
    run=session.execute(text(f'SELECT run_fondo FROM categoria_{kind} ORDER BY run_fondo LIMIT 1')).scalar_one()
    initial=m.fetch_fund(session,kind,run)
    choices=m.category_options(kind)
    choice=next(c for c in choices if c['code']!=initial['algorithm']['code'])
    change=m.Edit(action='lock',code=choice['code'],group=choice['grupo'],reason='Reviewed fund mandate',expected_version=initial['version'])
    saved=m.apply_edit(session,kind,run,change,'test-editor')
    assert saved['locked'] and saved['disagreement']
    assert saved['applied']['code']==choice['code'] and saved['updated_by']=='test-editor'
    assert m.fetch_fund(session,kind,run)['algorithm']==initial['algorithm']
    effective=session.execute(text(f'SELECT categoria,confianza FROM categoria_{kind}_effective WHERE run_fondo=:run ORDER BY periodo DESC LIMIT 1'),dict(run=run)).mappings().one()
    assert effective['categoria']==choice['code'] and effective['confianza']=='Manual'
    # A new period of algorithm output must not bypass the per-fund manual lock.
    automatic=next(c for c in choices if c['code']!=choice['code'])
    session.execute(text(f"""INSERT INTO categoria_{kind}(run_fondo,periodo,categoria,grupo,tipo,nombre_cat,confianza,updated_at)
        VALUES(:run,CURRENT_DATE,:code,:grupo,:tipo,:name,'Alta',now())
        ON CONFLICT(run_fondo,periodo) DO UPDATE SET categoria=EXCLUDED.categoria,grupo=EXCLUDED.grupo,
          tipo=EXCLUDED.tipo,nombre_cat=EXCLUDED.nombre_cat,confianza=EXCLUDED.confianza"""),dict(run=run,**automatic))
    after_job=m.fetch_fund(session,kind,run)
    assert after_job['algorithm']['code']==automatic['code']
    assert after_job['applied']['code']==choice['code'] and after_job['locked']
    assert session.execute(text(f'SELECT categoria FROM categoria_{kind}_effective WHERE run_fondo=:run ORDER BY periodo DESC LIMIT 1'),dict(run=run)).scalar_one()==choice['code']
    with pytest.raises(HTTPException) as stale:m.apply_edit(session,kind,run,change,'other-editor')
    assert stale.value.status_code==409
    unlocked=m.apply_edit(session,kind,run,m.Edit(action='unlock',reason='Restore latest algorithm',expected_version=saved['version']),'test-editor')
    assert not unlocked['locked'] and unlocked['applied']['code']==automatic['code']
    assert session.execute(text(f'SELECT categoria FROM categoria_{kind}_effective WHERE run_fondo=:run ORDER BY periodo DESC LIMIT 1'),dict(run=run)).scalar_one()==automatic['code']
    events=session.execute(text('SELECT action,actor,before_state,after_state FROM classification_audit WHERE kind=:kind AND run_fondo=:run ORDER BY id DESC LIMIT 2'),dict(kind=kind,run=run)).mappings().all()
    assert [e['action'] for e in events]==['unlock','lock']
    assert events[0]['before_state']['locked'] and not events[0]['after_state']['locked']
    assert all(e['actor']=='test-editor' for e in events)


@pytest.mark.parametrize('kind',['fm','fi'])
def test_invalid_category_does_not_write_and_uncategorized_fund_can_be_corrected(transaction,kind):
    session=transaction
    run=session.execute(text(f'SELECT run_fondo FROM categoria_{kind} ORDER BY run_fondo LIMIT 1')).scalar_one()
    before=m.fetch_fund(session,kind,run)
    invalid=m.Edit(action='lock',code='WRONG',group='Invalid',reason='Invalid code test',expected_version=before['version'])
    with pytest.raises(HTTPException) as error:m.apply_edit(session,kind,run,invalid,'test-editor')
    assert error.value.status_code==422 and m.fetch_fund(session,kind,run)==before
    session.execute(text(f'DELETE FROM categoria_{kind} WHERE run_fondo=:run'),dict(run=run))
    choice=m.category_options(kind)[0]
    result=m.apply_edit(session,kind,run,m.Edit(action='lock',code=choice['code'],group=choice['grupo'],reason='Classify missing fund',expected_version=before['version']),'test-editor')
    assert result['algorithm'] is None and result['applied']['code']==choice['code']
    assert session.execute(text(f'SELECT categoria FROM categoria_{kind}_effective WHERE run_fondo=:run'),dict(run=run)).scalar_one()==choice['code']


def test_authenticated_http_save_and_history_are_atomic(client,transaction,monkeypatch):
    from sqlalchemy.orm import Session
    session=transaction
    run=session.execute(text('SELECT run_fondo FROM categoria_fi ORDER BY run_fondo LIMIT 1')).scalar_one()
    initial=m.fetch_fund(session,'fi',run)
    # Each HTTP request gets a savepoint inside this test's rollback-only transaction.
    monkeypatch.setattr(m,'SessionLocal',lambda:Session(bind=session.connection(),join_transaction_mode='create_savepoint'))
    choice=next(c for c in m.category_options('fi') if c['code']!=initial['algorithm']['code'])
    auth={'Authorization':'Bearer test-key'}
    payload=dict(action='lock',code=choice['code'],group=choice['grupo'],reason='Authenticated endpoint test',expected_version=initial['version'])
    response=client.put('/classification-admin/funds/fi/'+run,headers=auth,json=payload)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['locked'] and response.json()['updated_by']=='test-editor'
    repeated=client.put('/classification-admin/funds/fi/'+run,headers=auth,json=payload)
    assert repeated.status_code==409
    detail=client.get('/classification-admin/funds/fi/'+run,headers=auth).json()
    assert detail['history'][0]['actor']=='test-editor'
    assert detail['history'][0]['after_state']['applied']['code']==choice['code']
    unlocked=client.put('/classification-admin/funds/fi/'+run,headers={'Authorization':'Bearer other-key'},json=dict(action='unlock',reason='Restore after endpoint test',expected_version=response.json()['version']))
    assert unlocked.status_code==200 and not unlocked.json()['locked']
    assert unlocked.json()['updated_by']=='other-editor'
    history=client.get('/classification-admin/funds/fi/'+run,headers=auth).json()['history']
    assert [event['actor'] for event in history[:2]]==['other-editor','test-editor']
