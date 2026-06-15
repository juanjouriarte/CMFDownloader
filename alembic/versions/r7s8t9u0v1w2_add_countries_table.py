"""add countries table

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Branch Labels: None
Depends On: None

CMF country code reference table scraped from:
https://www.cmfchile.cl/institucional/seil/certificacion_paises.php

Populated daily by the countries_refresh scheduler job.
Used to resolve codigo_pais_emisor / cod_pais codes to country names
in portfolio and geo_breakdown endpoints.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "r7s8t9u0v1w2"
down_revision: Union[str, None] = "q6r7s8t9u0v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            code        VARCHAR(5)   PRIMARY KEY,
            name        VARCHAR(100) NOT NULL,
            updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS countries")
