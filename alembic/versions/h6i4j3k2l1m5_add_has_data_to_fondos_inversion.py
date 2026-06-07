"""add has_data to fondos_inversion

Revision ID: h6i4j3k2l1m5
Revises: g5h3i2j4k1l6
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'h6i4j3k2l1m5'
down_revision: Union[str, None] = 'g5h3i2j4k1l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fondos_inversion', sa.Column('has_data', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('fondos_inversion', 'has_data')
