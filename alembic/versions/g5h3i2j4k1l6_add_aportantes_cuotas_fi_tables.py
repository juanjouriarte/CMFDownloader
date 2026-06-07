"""add aportantes_fi and cuotas_fi tables

Revision ID: g5h3i2j4k1l6
Revises: f3a2d8e5b1c7
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'g5h3i2j4k1l6'
down_revision: Union[str, None] = 'f3a2d8e5b1c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'aportantes_fi',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.Text(), nullable=True),
        sa.Column('tipo_persona', sa.String(1), nullable=True),
        sa.Column('rut', sa.String(20), nullable=True),
        sa.Column('dv_rut', sa.String(5), nullable=True),
        sa.Column('pct_propiedad', sa.Numeric(10, 4), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_fondo', 'periodo', 'rank', name='uq_aportante_fi'),
    )
    op.create_index('ix_aportantes_fi_run_fondo', 'aportantes_fi', ['run_fondo'])
    op.create_index('ix_aportantes_fi_periodo', 'aportantes_fi', ['periodo'])

    op.create_table(
        'cuotas_fi',
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('cuotas_emitidas', sa.BigInteger(), nullable=True),
        sa.Column('cuotas_pagadas', sa.BigInteger(), nullable=True),
        sa.Column('cuotas_suscritas_no_pagadas', sa.BigInteger(), nullable=True),
        sa.Column('num_cuotas_promesa', sa.BigInteger(), nullable=True),
        sa.Column('num_contratos_promesa', sa.BigInteger(), nullable=True),
        sa.Column('num_promitentes', sa.BigInteger(), nullable=True),
        sa.Column('valor_libro', sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint('run_fondo', 'periodo'),
    )
    op.create_index('ix_cuotas_fi_run_fondo', 'cuotas_fi', ['run_fondo'])


def downgrade() -> None:
    op.drop_index('ix_cuotas_fi_run_fondo', table_name='cuotas_fi')
    op.drop_table('cuotas_fi')
    op.drop_index('ix_aportantes_fi_periodo', table_name='aportantes_fi')
    op.drop_index('ix_aportantes_fi_run_fondo', table_name='aportantes_fi')
    op.drop_table('aportantes_fi')
