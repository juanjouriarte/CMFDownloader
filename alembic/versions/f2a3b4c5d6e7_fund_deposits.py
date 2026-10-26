"""CMF fund regulation deposits, successful imports and observed revisions."""
from alembic import op
revision = 'f2a3b4c5d6e7'
down_revision = 'e1f2a3b4c5d6'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE IF NOT EXISTS fund_deposits (
        registration TEXT NOT NULL, run TEXT NOT NULL, fund_name TEXT NOT NULL,
        administrator TEXT NOT NULL, deposit_date DATE NOT NULL, modified_date DATE NOT NULL,
        fund_type TEXT NOT NULL CHECK (fund_type IN ('FM','FIR','FNR')),
        active BOOLEAN NOT NULL, liquidated BOOLEAN NOT NULL,
        documents JSONB NOT NULL, approval_reference TEXT,
        source_present BOOLEAN NOT NULL DEFAULT true,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (registration,run))''')
    for name, columns in [('deposit','deposit_date DESC,registration,run'),
                          ('modified','modified_date DESC,registration,run'),
                          ('admin','administrator'),('type','fund_type')]:
        op.execute(f'CREATE INDEX IF NOT EXISTS ix_fund_deposits_{name} ON fund_deposits ({columns})')
    op.execute('''CREATE TABLE IF NOT EXISTS fund_deposit_imports (
        id BIGSERIAL PRIMARY KEY, fetched_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        row_count INTEGER NOT NULL, inserted INTEGER NOT NULL, updated INTEGER NOT NULL,
        source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL)''')
    op.execute('''CREATE TABLE IF NOT EXISTS fund_deposit_revisions (
        id BIGSERIAL PRIMARY KEY, registration TEXT NOT NULL, run TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL, snapshot JSONB NOT NULL,
        FOREIGN KEY (registration,run) REFERENCES fund_deposits(registration,run))''')
    op.execute('CREATE INDEX IF NOT EXISTS ix_fund_deposit_revisions_identity ON fund_deposit_revisions(registration,run,observed_at DESC)')


def downgrade():
    for table in ('fund_deposit_revisions','fund_deposit_imports','fund_deposits'):
        op.execute(f'DROP TABLE IF EXISTS {table}')
