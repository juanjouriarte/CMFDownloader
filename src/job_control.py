"""Shared execution context and cross-process locks for cron and manual jobs."""
from contextlib import contextmanager
from contextvars import ContextVar
from sqlalchemy import text
from src.db.engine import engine

request_id = ContextVar('job_request_id', default=None)


class JobBusy(Exception):
    pass


@contextmanager
def job_lock(job_id):
    # A dedicated connection holds this session lock through loader commits.
    with engine.connect() as connection:
        key = 'cmf-job:' + job_id
        acquired = connection.execute(text('SELECT pg_try_advisory_lock(hashtext(:key))'), {'key':key}).scalar()
        connection.commit()
        if not acquired:
            raise JobBusy(job_id)
        try:
            yield
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(hashtext(:key))'), {'key':key})
            connection.commit()
