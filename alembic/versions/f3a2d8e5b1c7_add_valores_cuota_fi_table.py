"""add valores_cuota_fi table

Revision ID: f3a2d8e5b1c7
Revises: e4f1a8b2c3d5
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f3a2d8e5b1c7'
down_revision: Union[str, None] = 'e4f1a8b2c3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'valores_cuota_fi',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('serie', sa.String(50), nullable=True),
        sa.Column('moneda', sa.String(10), nullable=True),
        sa.Column('valor_libro', sa.Numeric(20, 4), nullable=True),
        sa.Column('valor_economico', sa.Numeric(20, 4), nullable=True),
        sa.Column('patrimonio_neto', sa.BigInteger(), nullable=True),
        sa.Column('activo_total', sa.BigInteger(), nullable=True),
        sa.Column('num_aportantes', sa.Integer(), nullable=True),
        sa.Column('num_aportantes_inst', sa.Integer(), nullable=True),
        sa.Column('agencia', sa.String(50), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_fondo', 'fecha', 'serie', name='uq_valor_cuota_fi'),
    )
    op.create_index('ix_valores_cuota_fi_run_fondo', 'valores_cuota_fi', ['run_fondo'])
    op.create_index('ix_valores_cuota_fi_fecha', 'valores_cuota_fi', ['fecha'])


def downgrade() -> None:
    op.drop_index('ix_valores_cuota_fi_fecha', table_name='valores_cuota_fi')
    op.drop_index('ix_valores_cuota_fi_run_fondo', table_name='valores_cuota_fi')
    op.drop_table('valores_cuota_fi')
