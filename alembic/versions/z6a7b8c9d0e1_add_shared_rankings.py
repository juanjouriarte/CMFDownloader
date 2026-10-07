"""Shared ranking definitions and revision history.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
"""
from alembic import op

revision = 'z6a7b8c9d0e1'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS shared_rankings (
        id UUID PRIMARY KEY,
        config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
        created_by VARCHAR(120) NOT NULL,
        updated_by VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at TIMESTAMPTZ
    )""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_shared_rankings_updated ON shared_rankings(updated_at DESC) WHERE deleted_at IS NULL')
    op.execute("""CREATE TABLE IF NOT EXISTS shared_ranking_revisions (
        ranking_id UUID NOT NULL REFERENCES shared_rankings(id),
        version INTEGER NOT NULL,
        action VARCHAR(10) NOT NULL CHECK(action IN ('create','update','delete')),
        config JSONB NOT NULL,
        actor VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(ranking_id,version)
    )""")


def downgrade():
    op.execute('DROP TABLE IF EXISTS shared_ranking_revisions')
    op.execute('DROP TABLE IF EXISTS shared_rankings')
