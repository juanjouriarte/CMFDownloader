"""Durable administrator-triggered job queue and worker heartbeat."""
from alembic import op

revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS job_requests (
        id BIGSERIAL PRIMARY KEY,
        requested_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK(status IN ('queued','running','success','partial','error','interrupted','cancelled')),
        job_ids JSONB NOT NULL, current_job TEXT,
        completed INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        message TEXT)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_requests_active ON job_requests(status,id)")
    op.execute("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS request_id BIGINT REFERENCES job_requests(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_runs_request ON job_runs(request_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS job_runner_heartbeat (
        id INTEGER PRIMARY KEY CHECK(id=1), seen_at TIMESTAMPTZ NOT NULL)""")


def downgrade():
    op.execute('DROP TABLE IF EXISTS job_runner_heartbeat')
    op.execute('ALTER TABLE job_runs DROP COLUMN IF EXISTS request_id')
    op.execute('DROP TABLE IF EXISTS job_requests')
