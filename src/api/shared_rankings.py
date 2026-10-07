"""Database-backed team ranking definitions. Market data is calculated at read time."""
import hashlib
import hmac
import json
import os
import re
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from src.db.engine import SessionLocal

router = APIRouter(prefix='/shared-rankings', tags=['Shared rankings'])
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def require_editor(response: Response, credentials: Credentials):
    response.headers['Cache-Control'] = 'no-store'
    try:
        keys = json.loads(os.getenv('RANKINGS_KEYS') or os.getenv('CLASSIFICATION_ADMIN_KEYS', '{}'))
        if not isinstance(keys, dict) or not keys or any(
            not isinstance(k, str) or not k.strip() or len(k)>120 or
            not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}',v) for k,v in keys.items()):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, 'El acceso a rankings no está configurado.', headers={'Cache-Control':'no-store'})
    if credentials and credentials.scheme.lower() == 'bearer':
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        for actor, expected in keys.items():
            if hmac.compare_digest(digest, expected):
                return actor
    raise HTTPException(401, 'Clave de acceso inválida.', headers={'WWW-Authenticate':'Bearer','Cache-Control':'no-store'})


def require_reader(response: Response, credentials: Credentials):
    response.headers['Cache-Control'] = 'no-store'
    return require_editor(response, credentials)


Editor = Annotated[str, Depends(require_editor)]
Reader = Annotated[str | None, Depends(require_reader)]


class DateRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_: date = Field(alias='from')
    to: date

    @model_validator(mode='after')
    def validate_dates(self):
        if not date(2020,1,1) <= self.from_ <= self.to <= date.today():
            raise ValueError('Rango de fechas inválido.')
        return self


class RankingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    title: str = Field(min_length=1, max_length=80)
    metric: Literal['returns','nnm']
    kind: Literal['all','fm','fi']
    currency: str = Field(pattern=r'^(?:[A-Z]{3}|UF)$')
    period: Literal['1D','1W','1M','3M','6M','1A','5A','YTD']
    group: Literal['fund','admin','category']
    admin: str = Field(max_length=600)
    category: str = Field(max_length=600)
    fundKeys: list[str] = Field(default_factory=list, max_length=100)
    series: dict[str,list[str | None]] = Field(default_factory=dict)
    search: str = Field(max_length=600)
    direction: Literal['desc','asc']
    sort: Literal['value','name']
    limit: Literal[0,10,25,50,100]
    range: DateRange | None

    @model_validator(mode='after')
    def validate_selection(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError('Escribe un nombre para el ranking.')
        if self.metric == 'returns' and (self.period in ('3M','6M') or self.group != 'fund' or self.range is not None):
            raise ValueError('Configuración de rentabilidad inválida.')
        if any(not re.fullmatch(r'(fm|fi):\d{1,12}',key) or
               (self.kind != 'all' and not key.startswith(self.kind+':')) for key in self.fundKeys):
            raise ValueError('Identificador de fondo inválido.')
        if sum(len(values) for values in self.series.values())>500:
            raise ValueError('Máximo 500 series seleccionadas.')
        for key,values in self.series.items():
            if key not in self.fundKeys or any(value is not None and len(value)>64 for value in values):
                raise ValueError('Selección de series inválida.')
        if self.category:
            try:
                key=json.loads(self.category)
                if not isinstance(key,list) or len(key)!=4 or not all(isinstance(v,str) for v in key) or key[0] not in ('fm','fi') or (self.kind!='all' and key[0]!=self.kind):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('Categoría inválida.')
        self.fundKeys=list(dict.fromkeys(self.fundKeys))
        self.series={key:list(dict.fromkeys(values)) for key,values in self.series.items()}
        if self.fundKeys:
            self.admin=self.category=''
        return self


class CreateRanking(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    config: RankingConfig


class UpdateRanking(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)
    config: RankingConfig


def fetch_ranking(session, ranking_id):
    row=session.execute(text('SELECT * FROM shared_rankings WHERE id=:id AND deleted_at IS NULL'),dict(id=ranking_id)).mappings().first()
    if not row:
        raise HTTPException(404,'Ranking no encontrado.')
    return dict(row)


def record_revision(session, row, action, actor):
    session.execute(text('''INSERT INTO shared_ranking_revisions(ranking_id,version,action,config,actor)
        VALUES(:id,:version,:action,CAST(:config AS jsonb),:actor)'''),
        dict(id=row['id'],version=row['version'],action=action,config=json.dumps(row['config']),actor=actor))


@router.get('/session')
def session_info(editor: Editor):
    return dict(user=editor)


@router.get('')
def list_rankings(reader: Reader, limit: int=Query(100,ge=1,le=100), offset: int=Query(0,ge=0)):
    with SessionLocal() as session:
        total=session.execute(text('SELECT count(*) FROM shared_rankings WHERE deleted_at IS NULL')).scalar_one()
        rows=session.execute(text('''SELECT * FROM shared_rankings WHERE deleted_at IS NULL
            ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset'''),dict(limit=limit,offset=offset)).mappings().all()
        return dict(total=total,rows=[dict(row) for row in rows])


@router.get('/{ranking_id}')
def get_ranking(ranking_id: UUID, reader: Reader):
    with SessionLocal() as session:
        return fetch_ranking(session,ranking_id)


@router.post('', status_code=201)
def create_ranking(body: CreateRanking, editor: Editor):
    config=body.config.model_dump(mode='json',by_alias=True)
    with SessionLocal.begin() as session:
        row=session.execute(text('''INSERT INTO shared_rankings(id,config,created_by,updated_by)
            VALUES(:id,CAST(:config AS jsonb),:actor,:actor) ON CONFLICT(id) DO NOTHING RETURNING *'''),
            dict(id=body.id,config=json.dumps(config),actor=editor)).mappings().first()
        if row is None:
            existing=fetch_ranking(session,body.id)
            if existing['created_by']!=editor or existing['config']!=config:
                raise HTTPException(409,'Este identificador ya corresponde a otro ranking.')
            return existing
        result=dict(row)
        record_revision(session,result,'create',editor)
        return result


@router.put('/{ranking_id}')
def update_ranking(ranking_id: UUID, body: UpdateRanking, editor: Editor):
    with SessionLocal.begin() as session:
        row=session.execute(text('''UPDATE shared_rankings SET config=CAST(:config AS jsonb),
            version=version+1,updated_by=:actor,updated_at=now()
            WHERE id=:id AND version=:version AND deleted_at IS NULL RETURNING *'''),
            dict(id=ranking_id,version=body.expected_version,actor=editor,
                 config=body.config.model_dump_json(by_alias=True))).mappings().first()
        if row is None:
            fetch_ranking(session,ranking_id)
            raise HTTPException(409,'Otro editor cambió este ranking. Recarga la versión guardada antes de actualizar.')
        result=dict(row);record_revision(session,result,'update',editor)
        return result


@router.delete('/{ranking_id}')
def delete_ranking(ranking_id: UUID, editor: Editor, expected_version: int=Query(ge=1)):
    with SessionLocal.begin() as session:
        row=session.execute(text('''UPDATE shared_rankings SET deleted_at=now(),updated_at=now(),
            updated_by=:actor,version=version+1 WHERE id=:id AND version=:version
            AND deleted_at IS NULL RETURNING *'''),dict(id=ranking_id,version=expected_version,actor=editor)).mappings().first()
        if row is None:
            fetch_ranking(session,ranking_id)
            raise HTTPException(409,'Otro editor cambió este ranking. Recarga antes de eliminar.')
        record_revision(session,dict(row),'delete',editor)
        return dict(deleted=True)
