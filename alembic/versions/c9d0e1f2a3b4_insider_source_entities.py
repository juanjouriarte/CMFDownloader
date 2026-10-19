"""Preserve overlapping vigente/non-vigente issuer aliases.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from alembic import op
revision='c9d0e1f2a3b4'
down_revision='b8c9d0e1f2a3'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("ALTER TABLE insider_transactions ADD COLUMN IF NOT EXISTS source_entities JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("UPDATE insider_transactions SET source_entities=jsonb_build_array(jsonb_build_object('status',entity_status,'name',company_name)) WHERE source_entities='[]'::jsonb")


def downgrade():
    op.execute('ALTER TABLE insider_transactions DROP COLUMN IF EXISTS source_entities')
