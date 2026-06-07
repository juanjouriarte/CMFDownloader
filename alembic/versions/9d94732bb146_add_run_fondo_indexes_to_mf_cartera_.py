"""add run_fondo indexes to mf cartera tables

Revision ID: 9d94732bb146
Revises: k9l7m6n5o4p3
Create Date: 2026-06-07 01:04:51.488692

"""
from typing import Sequence, Union

from alembic import op


revision: str = '9d94732bb146'
down_revision: Union[str, None] = 'k9l7m6n5o4p3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_cartera_naci_run_fondo', 'cartera_naci', ['run_fondo'], unique=False)
    op.create_index('ix_cartera_extr_run_fondo', 'cartera_extr', ['run_fondo'], unique=False)
    op.create_index('ix_cartera_opci_run_fondo', 'cartera_opci', ['run_fondo'], unique=False)
    op.create_index('ix_cartera_futu_run_fondo', 'cartera_futu', ['run_fondo'], unique=False)
    op.create_index('ix_cartera_opla_run_fondo', 'cartera_opla', ['run_fondo'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_cartera_opla_run_fondo', table_name='cartera_opla')
    op.drop_index('ix_cartera_futu_run_fondo', table_name='cartera_futu')
    op.drop_index('ix_cartera_opci_run_fondo', table_name='cartera_opci')
    op.drop_index('ix_cartera_extr_run_fondo', table_name='cartera_extr')
    op.drop_index('ix_cartera_naci_run_fondo', table_name='cartera_naci')
