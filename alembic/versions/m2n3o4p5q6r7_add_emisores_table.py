"""add emisores table for SII company registry

Revision ID: m2n3o4p5q6r7
Revises: l1m2n3o4p5q6
Create Date: 2026-06-08

994k Chilean companies from SII registry. Used to enrich rut_emisor
in cartera_naci, cartera_fi_nac and rut in aportantes_fi with names.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m2n3o4p5q6r7"
down_revision: Union[str, None] = "l1m2n3o4p5q6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "emisores",
        sa.Column("rut", sa.String(20), primary_key=True),
        sa.Column("dv", sa.String(2), nullable=True),
        sa.Column("razon_social", sa.Text, nullable=False),
    )
    op.create_index("ix_emisores_razon_social", "emisores", ["razon_social"])


def downgrade() -> None:
    op.drop_index("ix_emisores_razon_social", "emisores")
    op.drop_table("emisores")
