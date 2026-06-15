"""add ref_codes table, migrate countries, drop countries

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-06-14
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "s8t9u0v1w2x3"
down_revision: Union[str, None] = "r7s8t9u0v1w2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ref_codes (
            id          SERIAL PRIMARY KEY,
            domain      VARCHAR(30)  NOT NULL,
            code        VARCHAR(50)  NOT NULL,
            name        VARCHAR(300) NOT NULL,
            updated_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_ref_codes_domain_code UNIQUE (domain, code)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ref_codes_domain ON ref_codes (domain)")

    # Migrate existing countries data
    op.execute("""
        INSERT INTO ref_codes (domain, code, name, updated_at)
        SELECT 'country', code, name, updated_at FROM countries
        ON CONFLICT (domain, code) DO UPDATE
            SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at
    """)

    op.execute("DROP TABLE IF EXISTS countries")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            code        VARCHAR(5)   PRIMARY KEY,
            name        VARCHAR(100) NOT NULL,
            updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        INSERT INTO countries (code, name, updated_at)
        SELECT code, name, updated_at FROM ref_codes WHERE domain = 'country'
        ON CONFLICT (code) DO UPDATE
            SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at
    """)
    op.execute("DELETE FROM ref_codes WHERE domain = 'country'")
    op.execute("DROP TABLE IF EXISTS ref_codes")
