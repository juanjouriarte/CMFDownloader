"""Single-consumer durable job queue. Run locally with python -m src.job_runner."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler
from src.db.engine import engine, SessionLocal
from src.job_catalog import CATALOG
from src.job_control import request_id, JobBusy

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='job-runner')
_future = None


@lru_cache(maxsize=1)
def job_functions():
    from src.scheduler import register_jobs, _run_tracked, _refresh_view
    scheduler = BackgroundScheduler(timezone='America/Santiago')
    register_jobs(scheduler)  # Registration only: never start a second cron scheduler.
    functions = {j.id:j.func for j in scheduler.get_jobs() if j.id in CATALOG}
    for name in set(CATALOG)-functions.keys():
        kind, suffix = name.split('_',1)
        view = f'mv_{suffix}_{kind}'
        functions[name] = lambda name=name,view=view: _run_tracked(name,lambda:_refresh_view(view))
    return functions


def recover_interrupted(session):
    # Called only while holding the exclusive consumer lock. No live queue
    # consumer can own these requests; never automatically replay a partial ETL.
    ids = session.execute(text("""UPDATE job_requests SET status='interrupted',
        finished_at=now(),current_job=NULL,message='El worker se detuvo antes de completar la solicitud.'
        WHERE status='running' RETURNING id""")).scalars().all()
    if ids:
        session.execute(text("""UPDATE job_runs SET status='interrupted',finished_at=now(),
            error_detail='WorkerInterrupted' WHERE request_id=ANY(:ids) AND status='running'"""),{'ids':ids})


def process_next(functions=None):
    with engine.connect() as lock:
        acquired = lock.execute(text("SELECT pg_try_advisory_lock(hashtext('cmf-job-consumer'))")).scalar()
        lock.commit()
        if not acquired:
            return
        try:
            with SessionLocal() as s:
                recover_interrupted(s)
                row = s.execute(text("""SELECT * FROM job_requests WHERE status='queued'
                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""")).mappings().first()
                if row is None:
                    s.commit()
                    return
                item = dict(row)
                s.execute(text("UPDATE job_requests SET status='running',started_at=now() WHERE id=:id"),item)
                s.commit()
            token = request_id.set(item['id'])
            errors = completed = 0
            try:
                funcs = functions if functions is not None else job_functions()
                for name in item['job_ids']:
                    with SessionLocal() as s:
                        s.execute(text('UPDATE job_requests SET current_job=:job WHERE id=:id'),{'job':name,'id':item['id']})
                        s.commit()
                    try:
                        if name not in CATALOG:
                            raise ValueError('Unknown queued job')
                        # Cron may start after enqueue. Wait for that same job,
                        # instead of duplicating it or dropping this request.
                        while True:
                            try:
                                result = funcs[name]()
                                break
                            except JobBusy:
                                time.sleep(5)
                        errors += bool(getattr(result,'errors',0))
                    except Exception:
                        logger.exception('Manual job failed: %s request=%s',name,item['id'])
                        errors += 1
                    completed += 1
                    with SessionLocal() as s:
                        s.execute(text('UPDATE job_requests SET completed=:n,error_count=:errors WHERE id=:id'),
                                  {'n':completed,'errors':errors,'id':item['id']})
                        s.commit()
                status = 'success' if not errors else 'error' if errors==completed else 'partial'
                with SessionLocal() as s:
                    s.execute(text("""UPDATE job_requests SET status=:status,finished_at=now(),current_job=NULL,
                        message=:message WHERE id=:id"""),{'id':item['id'],'status':status,
                        'message':f'{errors} trabajos con errores. Revisa el historial.' if errors else None})
                    s.commit()
            finally:
                request_id.reset(token)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtext('cmf-job-consumer'))"))
            lock.commit()


def poll():
    global _future
    with SessionLocal() as s:
        s.execute(text('''INSERT INTO job_runner_heartbeat(id,seen_at) VALUES(1,now())
            ON CONFLICT(id) DO UPDATE SET seen_at=EXCLUDED.seen_at'''))
        s.commit()
    if _future is None or _future.done():
        if _future is not None:
            try:
                _future.result()
            except Exception:
                logger.exception('Queue consumer failed; the next poll will recover its request')
        _future = _executor.submit(process_next)


def register_queue(scheduler):
    scheduler.add_job(poll,'interval',seconds=5,id='job_runner_poll',max_instances=1,coalesce=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    # Manual-only local consumer: no scheduled downloads or email reports.
    try:
        while True:
            poll()
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info('Waiting for the current request to finish before shutdown')
        _executor.shutdown(wait=True)
