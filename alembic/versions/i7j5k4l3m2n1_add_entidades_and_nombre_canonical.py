"""add entidades table and nombre_canonical to aportantes_fi

Revision ID: i7j5k4l3m2n1
Revises: h6i4j3k2l1m5
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'i7j5k4l3m2n1'
down_revision: Union[str, None] = 'h6i4j3k2l1m5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'entidades',
        sa.Column('rut', sa.String(20), nullable=False),
        sa.Column('nombre_canonical', sa.Text(), nullable=True),
        sa.Column('tipo_persona', sa.String(1), nullable=True),
        sa.PrimaryKeyConstraint('rut'),
    )
    op.add_column('aportantes_fi', sa.Column('nombre_canonical', sa.Text(), nullable=True))
    op.create_index('ix_aportantes_fi_rut', 'aportantes_fi', ['rut'])


def downgrade() -> None:
    op.drop_index('ix_aportantes_fi_rut', table_name='aportantes_fi')
    op.drop_column('aportantes_fi', 'nombre_canonical')
    op.drop_table('entidades')
