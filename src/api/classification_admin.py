"""Authenticated category overrides; classifiers retain their original output."""
import hashlib
import hmac
import json
import os
import re
from functools import lru_cache
from time import monotonic
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from src.db.engine import SessionLocal

router=APIRouter(prefix='/classification-admin', tags=['Classification admin'])
bearer=HTTPBearer(auto_error=False)
Kind=Literal['fm','fi']


def require_editor(response: Response, credentials: Annotated[HTTPAuthorizationCredentials|None,Depends(bearer)]):
    response.headers['Cache-Control']='no-store'
    try:
        keys=json.loads(os.getenv('CLASSIFICATION_ADMIN_KEYS','{}'))
        if not isinstance(keys,dict) or not keys or any(not isinstance(k,str) or not k.strip() or len(k)>120 or not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for k,v in keys.items()):
            raise ValueError()
    except (ValueError,TypeError):
        raise HTTPException(503,'La edición de clasificaciones no está configurada.',headers={'Cache-Control':'no-store'})
    if credentials and credentials.scheme.lower()=='bearer':
        digest=hashlib.sha256(credentials.credentials.encode()).hexdigest()
        for actor,expected in keys.items():
            if hmac.compare_digest(digest,expected):
                return actor
    raise HTTPException(401,'Clave de administrador inválida.',headers={'WWW-Authenticate':'Bearer','Cache-Control':'no-store'})

Editor=Annotated[str,Depends(require_editor)]


@lru_cache(maxsize=2)
def category_options(kind):
    if kind=='fi':
        from src.etl.investmentFunds.investmentFundsCategories import CATEGORIAS
        return [dict(code=code,name=c['nombre'],tipo=c['tipo'],grupo=c['grupo']) for code,c in CATEGORIAS.items()]
    from src.categories import CATEGORIAS
    from src.etl.mutualFunds.mutualFundsCategories import REGION_POR_CATEGORIA
    return [dict(code=code,name=c['nombre'],tipo=str(c['tipo']),grupo=group)
            for code,c in CATEGORIAS.items()
            for group in ([REGION_POR_CATEGORIA[code]] if REGION_POR_CATEGORIA.get(code)
                          else ['Nacional','Internacional','Mixto'])]


def fund_sql(kind):
    table,name,admin,active=('fondo_mutuo','nombre_fondo','razon_social_administradora','fecha_termino_operaciones IS NULL') if kind=='fm' else ('fondos_inversion','razon_social','administrador','vigente IS TRUE')
    return f"""SELECT f.run_fondo run, f.{name} name, f.{admin} admin,
        (f.{active}) vigente,
        CASE WHEN c.run_fondo IS NOT NULL THEN jsonb_build_object(
          'code',c.categoria,'name',c.nombre_cat,'tipo',c.tipo,'grupo',c.grupo,
          'confidence',c.confianza,'period',c.periodo) END algorithm,
        CASE WHEN o.active THEN jsonb_build_object('code',o.categoria,'name',o.nombre_cat,'tipo',o.tipo,'grupo',o.grupo)
             WHEN c.run_fondo IS NOT NULL THEN jsonb_build_object('code',c.categoria,'name',c.nombre_cat,'tipo',c.tipo,'grupo',c.grupo) END applied,
        COALESCE(o.active,false) locked, COALESCE(o.version,0) version,
        o.reason, o.updated_by, o.updated_at,
        COALESCE(o.active AND (c.run_fondo IS NULL OR (o.categoria,o.grupo,o.tipo,o.nombre_cat)
          IS DISTINCT FROM (c.categoria,c.grupo,c.tipo,c.nombre_cat)),false) disagreement
        FROM {table} f
        LEFT JOIN LATERAL (SELECT * FROM categoria_{kind} c WHERE c.run_fondo=f.run_fondo
          AND c.periodo<=CURRENT_DATE ORDER BY c.periodo DESC LIMIT 1) c ON true
        LEFT JOIN classification_overrides o ON o.kind='{kind}' AND o.run_fondo=f.run_fondo"""


def fetch_fund(session,kind,run):
    row=session.execute(text(fund_sql(kind)+' WHERE f.run_fondo=:run'),dict(run=run)).mappings().first()
    if row is None:
        raise HTTPException(404,'Fondo no encontrado.')
    return dict(row)


@router.get('/session')
def session_info(editor: Editor):
    return dict(user=editor)


@router.get('/catalog')
def catalog(editor: Editor,kind: Kind):
    return category_options(kind)


@router.get('/funds')
def funds(editor: Editor,kind: Kind='fi',search: str=Query('',max_length=150),
          status: Literal['all','manual','automatic','review','low']='all',
          active_only: bool=True,limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0)):
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        session.execute(text("SET LOCAL statement_timeout='10s'"))
        data=[dict(r) for r in session.execute(text(fund_sql(kind))).mappings()]
    needle=search.strip().casefold()
    filtered=[r for r in data if (not active_only or r['vigente'])
        and (not needle or needle in (' '.join([r['run'],r['name'] or '',r['admin'] or ''])).casefold())
        and (status=='all' or status=='manual' and r['locked'] or status=='automatic' and not r['locked']
             or status=='review' and r['disagreement'] or status=='low' and (not r['algorithm'] or r['algorithm']['confidence'] in ('Baja','Media')))]
    filtered.sort(key=lambda r:(not r['disagreement'],r['name'] or '',r['run']))
    return dict(total=len(filtered),rows=filtered[offset:offset+limit])


@router.get('/funds/{kind}/{run}')
def detail(kind: Kind,run: str,editor: Editor):
    with SessionLocal() as session:
        row=fetch_fund(session,kind,run)
        history=[dict(r) for r in session.execute(text('SELECT id,action,actor,reason,before_state,after_state,created_at FROM classification_audit WHERE kind=:kind AND run_fondo=:run ORDER BY id DESC LIMIT 100'),dict(kind=kind,run=run)).mappings()]
    return dict(**row,history=history)


class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action: Literal['lock','unlock']
    code: str|None=Field(None,max_length=30)
    group: str|None=Field(None,max_length=80)
    reason: str=Field(min_length=8,max_length=1000)
    expected_version: int=Field(ge=0)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls,value):
        value=value.strip()
        if len(value)<8:raise ValueError('Escribe un motivo de al menos 8 caracteres.')
        return value


def apply_edit(session,kind,run,edit,actor):
    # Includes the initially absent override row, preventing concurrent inserts.
    session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),dict(key=f'classification:{kind}:{run}'))
    before=fetch_fund(session,kind,run)
    if edit.expected_version!=before['version']:
        raise HTTPException(409,'Otro editor cambió este fondo. Recarga antes de guardar.')
    if edit.action=='unlock' and not before['locked']:
        raise HTTPException(409,'Este fondo ya usa la clasificación automática.')
    choice=next((c for c in category_options(kind) if c['code']==edit.code and c['grupo']==edit.group),None) if edit.action=='lock' else before['applied']
    if not choice:
        raise HTTPException(422,'Selecciona una categoría y grupo válidos para este tipo de fondo.')
    session.execute(text("""INSERT INTO classification_overrides
        (kind,run_fondo,active,categoria,grupo,tipo,nombre_cat,reason,updated_by,version)
        VALUES (:kind,:run,:active,:code,:grupo,:tipo,:name,:reason,:actor,:version)
        ON CONFLICT(kind,run_fondo) DO UPDATE SET active=EXCLUDED.active,categoria=EXCLUDED.categoria,
          grupo=EXCLUDED.grupo,tipo=EXCLUDED.tipo,nombre_cat=EXCLUDED.nombre_cat,reason=EXCLUDED.reason,
          updated_by=EXCLUDED.updated_by,updated_at=now(),version=EXCLUDED.version"""),
        dict(kind=kind,run=run,active=edit.action=='lock',**choice,reason=edit.reason,actor=actor,version=before['version']+1))
    after=fetch_fund(session,kind,run)
    session.execute(text("""INSERT INTO classification_audit(kind,run_fondo,action,actor,reason,before_state,after_state)
        VALUES(:kind,:run,:action,:actor,:reason,CAST(:before AS jsonb),CAST(:after AS jsonb))"""),
        dict(kind=kind,run=run,action=edit.action,actor=actor,reason=edit.reason,
             before=json.dumps(jsonable_encoder(before)),after=json.dumps(jsonable_encoder(after))))
    return after


@router.put('/funds/{kind}/{run}')
def update(kind: Kind,run: str,edit: Edit,editor: Editor):
    with SessionLocal() as session:
        session.execute(text("SET LOCAL statement_timeout='10s'"))
        result=apply_edit(session,kind,run,edit,editor)
        session.commit()
    return result


def classification_cache_epoch():
    # Shared DB revision makes edits invalidate every API worker's process cache.
    with SessionLocal() as session:
        revision=session.execute(text('SELECT COALESCE(MAX(id),0) FROM classification_audit')).scalar_one()
    return (int(monotonic()//60),revision)
