"""Persist editor-created classification paths without modifying classifiers."""
from alembic import op

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE IF NOT EXISTS classification_catalog_custom (
        kind VARCHAR(2) NOT NULL CHECK (kind IN ('fm','fi')),
        code VARCHAR(30) NOT NULL,
        tipo VARCHAR(80) NOT NULL,
        grupo VARCHAR(80) NOT NULL,
        name VARCHAR(160) NOT NULL,
        identity_key VARCHAR(64) NOT NULL UNIQUE,
        reason TEXT NOT NULL,
        created_by VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (kind, code))''')


def downgrade():
    op.execute('DROP TABLE IF EXISTS classification_catalog_custom')
