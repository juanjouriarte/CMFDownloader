"""add categoria_fm table

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Branch Labels: None
Depends On: None
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "q6r7s8t9u0v1"
down_revision: Union[str, None] = "p5q6r7s8t9u0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categoria_fm",
        sa.Column("run_fondo", sa.String(length=20), nullable=False),
        sa.Column("periodo", sa.Date(), nullable=False),
        sa.Column("categoria", sa.String(length=30), nullable=False),
        sa.Column("grupo", sa.String(length=40), nullable=False),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column("nombre_cat", sa.String(length=120), nullable=False),
        sa.Column("confianza", sa.String(length=10), nullable=False),
        sa.Column("pct_equity", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("pct_naci", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("pct_uf", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("pct_clp", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("wam_dias", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_fondo", "periodo"),
    )
    op.create_index("ix_categoria_fm_periodo", "categoria_fm", ["periodo"])
    op.create_index("ix_categoria_fm_categoria", "categoria_fm", ["categoria"])


def downgrade() -> None:
    op.drop_index("ix_categoria_fm_categoria", table_name="categoria_fm")
    op.drop_index("ix_categoria_fm_periodo", table_name="categoria_fm")
    op.drop_table("categoria_fm")
