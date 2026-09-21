"""add adjusted mutual-fund flow view and detector state

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op


revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fm_flow_adjustment_state (
            id SMALLINT PRIMARY KEY,
            last_data_date DATE,
            last_full_scan_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_fm_flow_adjustment_state_singleton CHECK (id = 1)
        )
    """)
    op.execute("""
        CREATE OR REPLACE VIEW fm_daily_flows_adjusted AS
        WITH reported AS (
            SELECT
                run_fondo,
                fecha,
                moneda,
                SUM(monto_aportado) AS aportes,
                SUM(monto_rescatado) AS rescates,
                SUM(monto_aportado - monto_rescatado) AS reported_nnm
            FROM cartola_diaria
            WHERE monto_aportado IS NOT NULL
            GROUP BY run_fondo, fecha, moneda
        ),
        adjustments AS (
            SELECT
                target_run_fondo AS run_fondo,
                event_date AS fecha,
                currency,
                SUM(amount) AS internal_migration
            FROM fm_flow_adjustments
            WHERE status IN ('auto_confirmed', 'confirmed')
            GROUP BY target_run_fondo, event_date, currency
        )
        SELECT
            reported.run_fondo,
            reported.fecha,
            reported.moneda,
            reported.aportes,
            reported.rescates,
            reported.reported_nnm,
            COALESCE(adjustments.internal_migration, 0) AS internal_migration,
            reported.reported_nnm
                - COALESCE(adjustments.internal_migration, 0) AS adjusted_nnm
        FROM reported
        LEFT JOIN adjustments
          ON adjustments.run_fondo = reported.run_fondo
         AND adjustments.fecha = reported.fecha
         AND adjustments.currency = CASE reported.moneda
             WHEN '$$' THEN 'CLP'
             WHEN 'PROM' THEN 'USD'
             ELSE reported.moneda
         END
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS fm_daily_flows_adjusted")
    op.execute("DROP TABLE IF EXISTS fm_flow_adjustment_state")
