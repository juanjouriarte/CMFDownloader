"""add rescatable and vigente to nemotecnicos_fi

Revision ID: d2e7b4c1f9a3
Revises: c5d8e2f3a1b4
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd2e7b4c1f9a3'
down_revision: Union[str, None] = 'c5d8e2f3a1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('nemotecnicos_fi', sa.Column('rescatable', sa.Boolean(), nullable=True))
    op.add_column('nemotecnicos_fi', sa.Column('vigente', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('nemotecnicos_fi', 'vigente')
    op.drop_column('nemotecnicos_fi', 'rescatable')
