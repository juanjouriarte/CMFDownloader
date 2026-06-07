"""add job_runs table

Revision ID: 2334f98e82b7
Revises: 9d94732bb146
Create Date: 2026-06-07 01:24:24.092424

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2334f98e82b7'
down_revision: Union[str, None] = '9d94732bb146'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('job_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.String(length=50), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('rows_upserted', sa.Integer(), nullable=True),
    sa.Column('errors', sa.Integer(), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_job_runs_job_id', 'job_runs', ['job_id'], unique=False)
    op.create_index('ix_job_runs_started_at', 'job_runs', ['started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_job_runs_started_at', table_name='job_runs')
    op.drop_index('ix_job_runs_job_id', table_name='job_runs')
    op.drop_table('job_runs')
