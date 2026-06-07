"""fix periodo to use last day of quarter instead of first

Revision ID: k9l7m6n5o4p3
Revises: j8k6l5m4n3o2
Create Date: 2026-06-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'k9l7m6n5o4p3'
down_revision: Union[str, None] = 'j8k6l5m4n3o2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Expression: move date from first-of-month to last-of-month
_TO_LAST = "(DATE_TRUNC('month', periodo) + INTERVAL '1 month - 1 day')::date"
_TO_FIRST = "DATE_TRUNC('month', periodo)::date"


def upgrade() -> None:
    for table in ('aportantes_fi', 'cartera_fi_nac', 'cartera_fi_ext',
                  'cartera_fi_met_part', 'cartera_fi_fut_fw'):
        op.execute(f"UPDATE {table} SET periodo = {_TO_LAST}")

    # cuotas_fi has composite PK (run_fondo, periodo) — need a temp column approach
    op.execute("""
        ALTER TABLE cuotas_fi ADD COLUMN periodo_new DATE;
        UPDATE cuotas_fi SET periodo_new = (DATE_TRUNC('month', periodo) + INTERVAL '1 month - 1 day')::date;
        ALTER TABLE cuotas_fi DROP CONSTRAINT cuotas_fi_pkey;
        ALTER TABLE cuotas_fi DROP COLUMN periodo;
        ALTER TABLE cuotas_fi RENAME COLUMN periodo_new TO periodo;
        ALTER TABLE cuotas_fi ADD PRIMARY KEY (run_fondo, periodo);
    """)


def downgrade() -> None:
    for table in ('aportantes_fi', 'cartera_fi_nac', 'cartera_fi_ext',
                  'cartera_fi_met_part', 'cartera_fi_fut_fw'):
        op.execute(f"UPDATE {table} SET periodo = {_TO_FIRST}")

    op.execute("""
        ALTER TABLE cuotas_fi ADD COLUMN periodo_new DATE;
        UPDATE cuotas_fi SET periodo_new = DATE_TRUNC('month', periodo)::date;
        ALTER TABLE cuotas_fi DROP CONSTRAINT cuotas_fi_pkey;
        ALTER TABLE cuotas_fi DROP COLUMN periodo;
        ALTER TABLE cuotas_fi RENAME COLUMN periodo_new TO periodo;
        ALTER TABLE cuotas_fi ADD PRIMARY KEY (run_fondo, periodo);
    """)
