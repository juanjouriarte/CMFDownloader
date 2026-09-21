"""add auditable mutual-fund flow adjustments

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op


revision: str = "v2w3x4y5z6a7"
down_revision: Union[str, None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fm_flow_adjustments (
            id BIGSERIAL PRIMARY KEY,
            source_run_fondo VARCHAR(20) NOT NULL,
            target_run_fondo VARCHAR(20) NOT NULL,
            event_date DATE NOT NULL,
            currency VARCHAR(10) NOT NULL,
            amount NUMERIC(28, 2) NOT NULL,
            source_final_aum NUMERIC(28, 2) NOT NULL,
            relative_difference NUMERIC(12, 10) NOT NULL,
            source_category VARCHAR(30) NOT NULL,
            target_category VARCHAR(30) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'auto_confirmed',
            reason TEXT NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fm_flow_adjustment_event UNIQUE (
                source_run_fondo, target_run_fondo, event_date, currency
            ),
            CONSTRAINT ck_fm_flow_adjustment_status CHECK (
                status IN ('auto_confirmed', 'confirmed', 'rejected')
            ),
            CONSTRAINT ck_fm_flow_adjustment_positive CHECK (amount > 0),
            CONSTRAINT ck_fm_flow_adjustment_distinct_funds CHECK (
                source_run_fondo <> target_run_fondo
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_fm_flow_adjustments_target_date
        ON fm_flow_adjustments (target_run_fondo, event_date, status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_fm_flow_adjustments_source
        ON fm_flow_adjustments (source_run_fondo)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fm_flow_adjustments")
