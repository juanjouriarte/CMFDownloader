"""add flujo_neto to valores_cuota_fi

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Branch Labels: None
Depends On: None

Adds flujo_neto (daily implied net flow in CLP) to valores_cuota_fi for
rescatable FI funds, analogous to monto_aportado/monto_rescatado in FM.

Formula: (cuotas_t - cuotas_{t-1}) * valor_libro_t
         where cuotas = patrimonio_neto / valor_libro

This strips the performance effect from the AUM change, leaving only the
actual investor flow. NULL for the first observation per (run_fondo, serie).

The backfill UPDATE runs a LAG() window over all 2.9M rows — expect ~2-5
minutes on the production DB.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "o4p5q6r7s8t9"
down_revision: Union[str, None] = "n3o4p5q6r7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS makes this safe to re-run on DBs where the column was applied manually.
    op.execute(
        "ALTER TABLE valores_cuota_fi "
        "ADD COLUMN IF NOT EXISTS flujo_neto NUMERIC(28, 4)"
    )

    # Regular (non-concurrent) index creation works inside a transaction and is
    # appropriate here — the container restarts during deploy anyway.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_valores_cuota_fi_fecha_run_flujo "
        "ON valores_cuota_fi (fecha, run_fondo, flujo_neto)"
    )

    # Backfill all existing rows in one pass using LAG() window function.
    # Safe to re-run: only updates rows where the computed flujo is NOT NULL.
    op.execute("""
        WITH lagged AS (
            SELECT
                run_fondo,
                serie,
                fecha,
                (
                    patrimonio_neto::numeric / NULLIF(valor_libro, 0)
                    - LAG(patrimonio_neto::numeric / NULLIF(valor_libro, 0))
                      OVER w
                ) * valor_libro AS flujo
            FROM valores_cuota_fi
            WHERE valor_libro IS NOT NULL
              AND valor_libro > 0
              AND patrimonio_neto IS NOT NULL
            WINDOW w AS (PARTITION BY run_fondo, serie ORDER BY fecha)
        )
        UPDATE valores_cuota_fi v
        SET flujo_neto = l.flujo
        FROM lagged l
        WHERE v.run_fondo = l.run_fondo
          AND v.serie IS NOT DISTINCT FROM l.serie
          AND v.fecha = l.fecha
          AND l.flujo IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_valores_cuota_fi_fecha_run_flujo")
    op.execute("ALTER TABLE valores_cuota_fi DROP COLUMN IF EXISTS flujo_neto")
