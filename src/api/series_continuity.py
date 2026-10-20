"""Reviewed FI series identities. Raw NAV is never rewritten."""
from datetime import date, timedelta
from decimal import Decimal
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import text

from src.db.engine import SessionLocal
from .classification_admin import Editor

router = APIRouter(prefix='/series-continuity', tags=['Series continuity'])


class SeriesLink(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    source_serie: str = Field(min_length=1, max_length=50)
    target_serie: str = Field(min_length=1, max_length=50)
    effective_date: date
    event_type: Literal['rename', 'conversion'] = 'rename'
    ratio: Decimal = Field(default=Decimal(1), ge=Decimal('0.000000000001'), le=Decimal('1000000000000'), allow_inf_nan=False)
    source_url: HttpUrl
    reason: str = Field(min_length=8, max_length=1000)

    @model_validator(mode='after')
    def valid_link(self):
        if self.source_serie == self.target_serie:
            raise ValueError('La serie de origen y destino deben ser distintas.')
        if self.effective_date > date.today():
            raise ValueError('La fecha efectiva no puede estar en el futuro.')
        if self.event_type == 'rename' and self.ratio != 1:
            raise ValueError('Un cambio de nombre conserva una cuota por cuota.')
        return self


class LinkEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=8, max_length=1000)
    links: list[SeriesLink] = Field(max_length=100)

    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        value = value.strip()
        if len(value) < 8:
            raise ValueError('Escribe un motivo de al menos 8 caracteres.')
        return value


def read_links(session, run=None):
    sql = 'SELECT * FROM fi_series_links'
    return [dict(r, ratio=str(r['ratio'])) for r in session.execute(text(sql + (' WHERE run_fondo=:run' if run else '') + ' ORDER BY effective_date,source_serie'), dict(run=run)).mappings()]


def series_history(links, serie, as_of=None):
    """Chronological segments, with dates and evidence, for a selected identity."""
    incoming = {l['target_serie']: l for l in links}
    chain, seen, end = [], set(), as_of
    while serie is not None and serie not in seen:
        seen.add(serie)
        link = incoming.get(serie)
        start = link['effective_date'] if link else None
        chain.append(dict(serie=serie, start=start, end=end,
                          event_type=link['event_type'] if link else None,
                          ratio=str(link['ratio']) if link else None,
                          source_url=str(link['source_url']) if link else None))
        if not link:
            break
        end, serie = start - timedelta(days=1), link['source_serie']
    return list(reversed(chain)) if len(chain) > 1 else []


def validate_chain(links):
    sources, targets = {}, {}
    for link in links:
        if link.source_serie in sources or link.target_serie in targets:
            raise HTTPException(422, 'Cada serie admite un único origen y un único destino. No se permiten fusiones o bifurcaciones.')
        sources[link.source_serie] = link
        targets[link.target_serie] = link
    for start in sources:
        seen, current, previous_date = set(), start, None
        while current in sources:
            if current in seen:
                raise HTTPException(422, 'La cadena contiene un ciclo.')
            seen.add(current)
            if len(seen) >= 32:
                raise HTTPException(422, 'La cadena admite hasta 32 series.')
            link = sources[current]
            if previous_date and link.effective_date <= previous_date:
                raise HTTPException(422, 'Las fechas deben avanzar en el orden de la cadena.')
            previous_date, current = link.effective_date, link.target_serie


def fund_state(session, run, private=False):
    fund = session.execute(text('SELECT run_fondo run,razon_social name FROM fondos_inversion WHERE run_fondo=:run'), dict(run=run)).mappings().first()
    if not fund:
        raise HTTPException(404, 'Fondo no encontrado.')
    state = session.execute(text('SELECT version,updated_by,updated_at FROM fi_series_link_sets WHERE run_fondo=:run'), dict(run=run)).mappings().first()
    links = [{k:v for k,v in link.items() if k!='run_fondo'} for link in read_links(session, run)]
    data = dict(fund, **(dict(state) if state else dict(version=0, updated_by=None, updated_at=None)), links=links)
    if private:
        data['series'] = [dict(r) for r in session.execute(text('''SELECT serie,min(fecha) first_date,max(fecha) last_date,
          count(*) observations FROM valores_cuota_fi WHERE run_fondo=:run AND serie IS NOT NULL
          GROUP BY serie ORDER BY serie'''), dict(run=run)).mappings()]
        linked_sources = {l['source_serie'] for l in links}
        linked_targets = {l['target_serie'] for l in links}
        data['candidates'] = [dict(source_serie=a['serie'],target_serie=b['serie'],
            effective_date=b['first_date'],previous_date=a['last_date'])
            for a in data['series'] for b in data['series']
            if a['serie'] != b['serie'] and a['serie'] not in linked_sources
            and b['serie'] not in linked_targets
            and 0 < (b['first_date']-a['last_date']).days <= 7]
        data['audit'] = [dict(r) for r in session.execute(text('''SELECT version,actor,reason,before_state,after_state,created_at
          FROM fi_series_link_audit WHERE run_fondo=:run ORDER BY version DESC LIMIT 50'''), dict(run=run)).mappings()]
    return data


def validate_observations(session, run, links):
    validate_chain(links)
    boundaries = []
    for link in links:
        args = dict(run=run, source=link.source_serie, target=link.target_serie, day=link.effective_date)
        # Existing positive NAV on the wrong side means this is not a whole-series continuation.
        overlap = session.execute(text('''SELECT EXISTS(SELECT 1 FROM valores_cuota_fi
          WHERE run_fondo=:run AND valor_libro>0 AND valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
          AND ((serie=:source AND fecha>=:day) OR (serie=:target AND fecha<:day)))'''), args).scalar_one()
        if overlap:
            raise HTTPException(422, f'{link.source_serie} → {link.target_serie}: hay NAV fuera de las fechas del enlace. Revisa la fecha; una conversión parcial no permite unir historias completas.')
        previous = session.execute(text('''SELECT fecha,valor_libro,patrimonio_neto,fi_normalized_currency(moneda) currency
          FROM valores_cuota_fi WHERE run_fondo=:run AND serie=:source AND fecha<:day
          ORDER BY fecha DESC LIMIT 1'''), args).mappings().first()
        current = session.execute(text('''SELECT fecha,valor_libro,patrimonio_neto,fi_normalized_currency(moneda) currency
          FROM valores_cuota_fi WHERE run_fondo=:run AND serie=:target AND fecha>=:day
          ORDER BY fecha LIMIT 1'''), args).mappings().first()
        if not previous or not current or current['fecha'] != link.effective_date or (current['fecha']-previous['fecha']).days > 7:
            raise HTTPException(422, f'{link.source_serie} → {link.target_serie}: se necesita NAV en la fecha efectiva y una base dentro de los siete días anteriores.')
        if any(r['valor_libro'] is None or not r['valor_libro'].is_finite() or r['valor_libro'] <= 0 or r['patrimonio_neto'] is None or r['patrimonio_neto'] < 0 for r in (previous,current)):
            raise HTTPException(422, 'Los valores de cuota y patrimonio del enlace deben ser válidos.')
        if not previous['currency'] or previous['currency'] != current['currency']:
            raise HTTPException(422, 'No se enlazan series con monedas distintas o sin identificar.')
        boundaries.append(dict(source_serie=link.source_serie,target_serie=link.target_serie,
            previous=dict(previous),current=dict(current),ratio=link.ratio,
            nav_change_pct=(current['valor_libro']*link.ratio/previous['valor_libro']-1)*100))
    return boundaries


def apply_edit(session, run, edit, actor):
    session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), dict(key='series-continuity:'+run))
    before = fund_state(session, run)
    if before['version'] != edit.expected_version:
        raise HTTPException(409, 'Otro editor cambió estos enlaces. Recarga antes de guardar.')
    validate_observations(session, run, edit.links)
    session.execute(text('''INSERT INTO fi_series_link_sets(run_fondo,version,updated_by)
      VALUES(:run,:version,:actor) ON CONFLICT(run_fondo) DO UPDATE SET
      version=EXCLUDED.version,updated_by=EXCLUDED.updated_by,updated_at=now()'''),
      dict(run=run,version=before['version']+1,actor=actor))
    session.execute(text('DELETE FROM fi_series_links WHERE run_fondo=:run'), dict(run=run))
    for link in edit.links:
        session.execute(text('''INSERT INTO fi_series_links
          (run_fondo,source_serie,target_serie,effective_date,event_type,ratio,source_url,reason)
          VALUES(:run,:source_serie,:target_serie,:effective_date,:event_type,:ratio,:source_url,:reason)'''),
          dict(run=run,**link.model_dump(exclude={'source_url'}),source_url=str(link.source_url)))
    after = fund_state(session, run)
    session.execute(text('''INSERT INTO fi_series_link_audit(run_fondo,version,actor,reason,before_state,after_state)
      VALUES(:run,:version,:actor,:reason,CAST(:before AS jsonb),CAST(:after AS jsonb))'''),
      dict(run=run,version=after['version'],actor=actor,reason=edit.reason,
           before=json.dumps(jsonable_encoder(before['links'])),after=json.dumps(jsonable_encoder(after['links']))))
    return after


@router.get('/fi/{run}')
def public_history(run: str, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with SessionLocal() as session:
        state = fund_state(session, run)
        # Editor identity and audit trail stay in the authenticated endpoint.
        return dict(run=run, name=state['name'], version=state['version'],links=state['links'])


@router.get('/admin/fi/{run}')
def admin_history(run: str, editor: Editor):
    with SessionLocal() as session:
        return fund_state(session, run, private=True)


@router.post('/admin/fi/{run}/preview')
def preview(run: str, edit: LinkEdit, editor: Editor):
    with SessionLocal() as session:
        state = fund_state(session, run)
        if state['version'] != edit.expected_version:
            raise HTTPException(409, 'Recarga los enlaces antes de revisar.')
        return dict(boundaries=validate_observations(session, run, edit.links))


@router.put('/admin/fi/{run}')
def save(run: str, edit: LinkEdit, editor: Editor):
    with SessionLocal() as session:
        session.execute(text("SET LOCAL statement_timeout='20s'"))
        result = apply_edit(session, run, edit, editor)
        session.commit()
        return result
