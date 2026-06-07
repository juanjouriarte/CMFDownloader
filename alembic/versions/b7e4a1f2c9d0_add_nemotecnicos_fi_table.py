"""add nemotecnicos_fi table

Revision ID: b7e4a1f2c9d0
Revises: a3f9c2d1e5b8
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b7e4a1f2c9d0'
down_revision: Union[str, None] = 'a3f9c2d1e5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nemotecnicos_fi',
        sa.Column('nemotecnico', sa.String(20), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('dv_fondo', sa.String(5), nullable=True),
        sa.Column('razon_social', sa.String(255), nullable=True),
        sa.Column('nombre_fondo', sa.String(255), nullable=True),
        sa.Column('serie', sa.String(50), nullable=True),
        sa.Column('admin_rut', sa.String(20), nullable=True),
        sa.Column('admin_dv', sa.String(5), nullable=True),
        sa.Column('admin_nombre', sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint('nemotecnico'),
    )


def downgrade() -> None:
    op.drop_table('nemotecnicos_fi')
