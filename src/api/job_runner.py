"""Administrator-only data-job controls; the API queues work, never executes ETL."""
import json
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from src.api.classification_admin import Editor
from src.db.engine import SessionLocal
from src.job_catalog import CATALOG, plan_for, affected_jobs

router = APIRouter(prefix='/job-runner', tags=['Job runner'])


def worker_status(session):
    seen = session.execute(text('SELECT seen_at FROM job_runner_heartbeat WHERE id=1')).scalar()
    return {'online':bool(seen and (datetime.now(timezone.utc)-seen).total_seconds()<60), 'last_seen':seen}


def public_run(row):
    result = dict(row)
    detail = result.pop('error_detail', None)
    if result['status']=='success' and result.get('errors'):
        result['status']='partial'
    # Do not publish tracebacks, SQL, source URLs or credentials in error messages.
    result['message'] = None
    if result['status'] in ('error','interrupted'):
        kind = re.match(r'^[\w.]+', (detail or '').strip().split('\n')[-1])
        result['message'] = 'El proceso no terminó correctamente.' + (f' Tipo: {kind[0][:100]}.' if kind else '')
    elif result.get('errors'):
        result['message'] = f"{result['errors']} errores de descarga; se conservaron los registros válidos."
    return result


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1, max_length=50)


def enqueue(session, job_id, actor):
    try:
        plan = plan_for(job_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('cmf-job-enqueue'))"))
    if not worker_status(session)['online']:
        raise HTTPException(503, 'El ejecutor está desconectado. Inicia el worker antes de ejecutar trabajos.')
    affected = affected_jobs(plan)
    active = session.execute(text("SELECT id,job_ids FROM job_requests WHERE status IN ('queued','running')")).mappings()
    if any(affected.intersection(affected_jobs(r['job_ids'])) for r in active):
        raise HTTPException(409, 'Ya hay una solicitud pendiente o en ejecución para estos trabajos.')
    for name in sorted(affected):
        available = session.execute(text('SELECT pg_try_advisory_xact_lock(hashtext(:key))'), {'key':'cmf-job:'+name}).scalar()
        if not available:
            raise HTTPException(409, 'Uno de estos trabajos ya se está ejecutando. Espera a que termine.')
    return dict(session.execute(text("""INSERT INTO job_requests(requested_by,job_ids)
        VALUES (:actor,CAST(:plan AS jsonb)) RETURNING *"""), {'actor':actor,'plan':json.dumps(plan)}).mappings().one())


@router.get('/jobs')
def jobs(editor: Editor):
    with SessionLocal() as s:
        worker = worker_status(s)
        latest = {r['job_id']:public_run(r) for r in s.execute(text('SELECT DISTINCT ON(job_id) * FROM job_runs ORDER BY job_id,started_at DESC,id DESC')).mappings()}
        for name, run in latest.items():
            if name in CATALOG and run['status']=='running':
                free = s.execute(text('SELECT pg_try_advisory_xact_lock(hashtext(:key))'),{'key':'cmf-job:'+name}).scalar()
                if free:
                    run.update(status='interrupted',message='El proceso anterior ya no está activo.')
        active = [dict(r) for r in s.execute(text("SELECT * FROM job_requests WHERE status IN ('queued','running') ORDER BY id")).mappings()]
        recent = [dict(r) for r in s.execute(text('SELECT * FROM job_requests ORDER BY id DESC LIMIT 20')).mappings()]
    return dict(user=editor,worker=worker,jobs=[dict(**entry,last_run=latest.get(name),
        pending=next((r['id'] for r in active if name in affected_jobs(r['job_ids'])),None))
        for name,entry in CATALOG.items()],requests=recent)


@router.post('/requests', status_code=202)
def request_run(body: RunRequest, editor: Editor):
    with SessionLocal() as s:
        result = enqueue(s, body.job_id, editor)
        s.commit()
        return result


@router.delete('/requests/{request_id}')
def cancel(request_id: int, editor: Editor):
    with SessionLocal() as s:
        row = s.execute(text("""UPDATE job_requests SET status='cancelled',finished_at=now(),
            message=:message WHERE id=:id AND status='queued' RETURNING id"""),
            {'id':request_id,'message':f'Cancelada por {editor}.'}).scalar()
        if row is None:
            raise HTTPException(409, 'Sólo se pueden cancelar solicitudes que aún están en cola.')
        s.commit()
    return {'id':row,'status':'cancelled'}


@router.get('/jobs/{job_id}/runs')
def history(job_id: str, editor: Editor):
    if job_id not in CATALOG:
        raise HTTPException(404,'Trabajo no disponible.')
    with SessionLocal() as s:
        return [public_run(r) for r in s.execute(text('''SELECT j.*,r.requested_by
            FROM job_runs j LEFT JOIN job_requests r ON r.id=j.request_id
            WHERE job_id=:job ORDER BY started_at DESC,j.id DESC LIMIT 20'''),{'job':job_id}).mappings()]
