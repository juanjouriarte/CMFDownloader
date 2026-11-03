"""Reported quarterly FI equity, separate from daily NAV and portfolio assets."""
from alembic import op

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE IF NOT EXISTS fi_reported_equity (
        run_fondo TEXT NOT NULL, periodo DATE NOT NULL, currency TEXT NOT NULL,
        equity NUMERIC(32,4) NOT NULL, assets NUMERIC(32,4) NOT NULL,
        liabilities NUMERIC(32,4) NOT NULL, source_currency TEXT NOT NULL,
        source_scale INTEGER NOT NULL CHECK(source_scale=1000),
        source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(run_fondo,periodo), CHECK(assets>=0), CHECK(liabilities>=0))''')


def downgrade():
    op.execute('DROP TABLE IF EXISTS fi_reported_equity')
