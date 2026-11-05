"""Administrator-only data-job controls; the API queues work, never executes ETL."""
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from src.api.classification_admin import Editor
from src.db.engine import SessionLocal
from src.job_catalog import CATALOG, plan_for, affected_jobs

router = APIRouter(prefix='/job-runner', tags=['Job runner'])


def worker_status(session):
    seen = session.execute(text('SELECT seen_at FROM job_runner_heartbeat WHERE id=1')).scalar()
    return {'online':bool(seen and (datetime.now(timezone.utc)-seen).total_seconds()<60), 'last_seen':seen}


ERROR_HELP = {
    'TimeoutError': ('Tiempo de espera agotado.', 'Vuelve a ejecutar el trabajo cuando la fuente responda.'),
    'Timeout': ('Tiempo de espera agotado.', 'Vuelve a ejecutar el trabajo cuando la fuente responda.'),
    'ReadTimeout': ('La fuente tardó demasiado en responder.', 'Reintenta la descarga; si se repite, revisa la conexión con la fuente.'),
    'ConnectTimeout': ('No se pudo conectar con la fuente a tiempo.', 'Revisa la disponibilidad de la fuente y vuelve a ejecutar.'),
    'ConnectionError': ('No se pudo completar la conexión con la fuente.', 'Revisa la conexión y vuelve a ejecutar el trabajo.'),
    'HTTPError': ('La fuente devolvió una respuesta HTTP no válida.', 'Revisa su disponibilidad o acceso antes de reintentar.'),
    'OperationalError': ('Falló una operación o conexión con la base de datos.', 'Revisa el servicio de base de datos antes de reintentar.'),
    'IntegrityError': ('Los datos no cumplen una restricción de la base de datos.', 'Revisa el importador y los datos de origen.'),
    'ProgrammingError': ('Falló una consulta a la base de datos.', 'Revisa la consulta y las migraciones del backend.'),
    'ValueError': ('Se recibió un valor o formato no válido.', 'Revisa el formato publicado por la fuente y el importador.'),
    'InvalidReport': ('El informe no superó la validación.', 'Revisa el archivo publicado por la fuente.'),
    'RuntimeError': ('El proceso no pudo completar la operación.', 'Revisa el registro del servidor con el identificador de esta ejecución.'),
    'KeyError': ('Falta un campo esperado en los datos.', 'Revisa si cambió el formato publicado por la fuente.'),
    'TypeError': ('Se recibió un tipo de dato inesperado.', 'Revisa el importador antes de reintentar.'),
}


def public_run(row):
    result = dict(row)
    detail = result.pop('error_detail', None)
    if result['status']=='success' and result.get('errors'):
        result['status']='partial'
    result.update(message=None,error_type=None,error_summary=None,suggested_action=None)
    # Only allowlisted exception names and fixed guidance leave the server.
    # Tracebacks can contain SQL, credentials, URLs and source-file contents.
    if result['status']=='interrupted':
        result.update(message='El proceso anterior ya no está activo.',
            error_summary='La ejecución se interrumpió antes de terminar.',
            suggested_action='Comprueba que el ejecutor esté conectado y vuelve a ejecutar el trabajo.')
    elif result['status']=='error':
        last=(detail or '').strip().split('\n')[-1]
        match=re.match(r'^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*):',last)
        kind=match[1] if match and match[1] in ERROR_HELP else None
        summary,action=ERROR_HELP.get(kind,('El proceso terminó con un error.',
            'Revisa el registro del servidor con el identificador de esta ejecución.'))
        result.update(message='El proceso no terminó correctamente.'+(f' Tipo: {kind}.' if kind else ''),
            error_type=kind,error_summary=summary,suggested_action=action)
    elif result['status']=='partial':
        result.update(message=f"{result['errors']} errores de descarga; se conservaron los registros válidos." if result.get('errors') else 'El trabajo terminó con incidencias.',
            error_summary='La actualización se completó parcialmente.',
            suggested_action='Revisa la cobertura de los datos; puedes volver a ejecutar el trabajo.')
    return result


def inactive_jobs(session):
    """Detect abandoned runs without changing persisted history or starting jobs."""
    running=session.execute(text("SELECT DISTINCT job_id FROM job_runs WHERE status='running' AND job_id=ANY(:jobs)"),{'jobs':list(CATALOG)}).scalars()
    return [job for job in running if session.execute(text('SELECT pg_try_advisory_xact_lock(hashtext(:key))'),
        {'key':'cmf-job:'+job}).scalar()]


@router.get('/runs')
def run_log(editor: Editor, job_id: str|None=None,
        status: Literal['all','incidents','error','partial','interrupted','success','running']='all',
        days: int=Query(7,ge=0,le=3650), request_id: int|None=Query(None,ge=1),
        limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0,le=100000)):
    if job_id is not None and job_id not in CATALOG:
        raise HTTPException(404,'Trabajo no disponible.')
    with SessionLocal() as s:
        args=dict(jobs=list(CATALOG),job=job_id,status=status,request=request_id,
            since=datetime.now(timezone.utc)-timedelta(days=days) if days else None,
            inactive=inactive_jobs(s),limit=limit,offset=offset)
        cte="""WITH effective AS (
            SELECT j.*,r.requested_by,
                CASE WHEN j.status='running' AND j.job_id=ANY(:inactive) THEN 'interrupted'
                     WHEN j.status='success' AND COALESCE(j.errors,0)>0 THEN 'partial'
                     ELSE j.status END display_status
            FROM job_runs j LEFT JOIN job_requests r ON r.id=j.request_id
            WHERE j.job_id=ANY(:jobs) AND (:job IS NULL OR j.job_id=:job)
                AND (:since IS NULL OR j.started_at>=:since)
                AND (:request IS NULL OR j.request_id=:request)
        ) """
        summary={r['display_status']:r['n'] for r in s.execute(text(cte+
            'SELECT display_status,COUNT(*) n FROM effective GROUP BY display_status'),args).mappings()}
        condition=" WHERE (:status='all' OR display_status=:status OR (:status='incidents' AND display_status IN ('error','partial','interrupted')))"
        total=s.execute(text(cte+'SELECT COUNT(*) FROM effective'+condition),args).scalar()
        records=s.execute(text(cte+'SELECT * FROM effective'+condition+
            ' ORDER BY started_at DESC,id DESC LIMIT :limit OFFSET :offset'),args).mappings()
        results=[]
        for record in records:
            row=dict(record);row['status']=row.pop('display_status')
            results.append(dict(public_run(row),name=CATALOG[row['job_id']]['name'],group=CATALOG[row['job_id']]['group']))
    return dict(rows=results,total=total,summary=summary,limit=limit,offset=offset)


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
                    run.update(public_run(dict(run,status='interrupted')))
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
        inactive=inactive_jobs(s)
        return [public_run(dict(r,status='interrupted') if r['status']=='running' and r['job_id'] in inactive else r) for r in s.execute(text('''SELECT j.*,r.requested_by
            FROM job_runs j LEFT JOIN job_requests r ON r.id=j.request_id
            WHERE job_id=:job ORDER BY started_at DESC,j.id DESC LIMIT 20'''),{'job':job_id}).mappings()]
