"""add dividendos table

Revision ID: c5d8e2f3a1b4
Revises: b7e4a1f2c9d0
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c5d8e2f3a1b4'
down_revision: Union[str, None] = 'b7e4a1f2c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dividendos',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('nemo', sa.String(30), nullable=False),
        sa.Column('descrip_vc', sa.Text(), nullable=True),
        sa.Column('fec_lim', sa.String(20), nullable=True),
        sa.Column('fec_pago', sa.String(20), nullable=True),
        sa.Column('val_acc', sa.Numeric(20, 6), nullable=True),
        sa.Column('moneda', sa.String(10), nullable=True),
        sa.Column('num_acc_ant', sa.BigInteger(), nullable=True),
        sa.Column('num_acc_der', sa.BigInteger(), nullable=True),
        sa.Column('num_acc_nue', sa.BigInteger(), nullable=True),
        sa.Column('pre_ant_vc', sa.Numeric(20, 6), nullable=True),
        sa.Column('pre_ex_vc', sa.Numeric(20, 6), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nemo', 'fec_pago', 'descrip_vc', name='uq_dividendo'),
    )
    op.create_index('ix_dividendos_nemo', 'dividendos', ['nemo'])
    op.create_index('ix_dividendos_fec_pago', 'dividendos', ['fec_pago'])


def downgrade() -> None:
    op.drop_index('ix_dividendos_fec_pago', table_name='dividendos')
    op.drop_index('ix_dividendos_nemo', table_name='dividendos')
    op.drop_table('dividendos')
