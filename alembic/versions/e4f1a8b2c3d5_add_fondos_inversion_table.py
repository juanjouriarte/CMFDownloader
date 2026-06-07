"""add fondos_inversion table and drop rescatable/vigente from nemotecnicos_fi

Revision ID: e4f1a8b2c3d5
Revises: d2e7b4c1f9a3
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e4f1a8b2c3d5'
down_revision: Union[str, None] = 'd2e7b4c1f9a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'fondos_inversion',
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('dv_fondo', sa.String(5), nullable=True),
        sa.Column('razon_social', sa.Text(), nullable=True),
        sa.Column('administrador', sa.Text(), nullable=True),
        sa.Column('rescatable', sa.Boolean(), nullable=True),
        sa.Column('vigente', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('run_fondo'),
    )
    op.drop_column('nemotecnicos_fi', 'rescatable')
    op.drop_column('nemotecnicos_fi', 'vigente')


def downgrade() -> None:
    op.add_column('nemotecnicos_fi', sa.Column('vigente', sa.Boolean(), nullable=True))
    op.add_column('nemotecnicos_fi', sa.Column('rescatable', sa.Boolean(), nullable=True))
    op.drop_table('fondos_inversion')
