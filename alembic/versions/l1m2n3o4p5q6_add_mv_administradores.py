"""add mv_administradores materialized view

Revision ID: l1m2n3o4p5q6
Revises: 74221d60d813
Create Date: 2026-06-07 00:00:00.000000

Unified administrator dimension built from existing tables:
  - fondo_mutuo         → FM admins (has rut + nombre)
  - nemotecnicos_fi     → FI admins (has rut + nombre)
  - fondos_inversion    → FI fund counts (name-matched)

No new data source needed — derived entirely from existing tables.
Refresh daily alongside fm_identity / fi_identity jobs.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "l1m2n3o4p5q6"
down_revision: Union[str, None] = "74221d60d813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIEW = "mv_administradores"

_SQL = """
CREATE MATERIALIZED VIEW mv_administradores AS
WITH fm_admins AS (
    SELECT
        rut_administradora                              AS rut,
        MAX(razon_social_administradora)                AS nombre,
        COUNT(*)                                        AS funds_fm,
        SUM(CASE WHEN fecha_termino_operaciones IS NULL
                 THEN 1 ELSE 0 END)                    AS funds_fm_vigente
    FROM fondo_mutuo
    GROUP BY rut_administradora
),
fi_admins AS (
    -- nemotecnicos_fi.admin_rut is not populated by CMF; use fondos_inversion.administrador
    SELECT
        administrador                                   AS nombre_fi,
        COUNT(*)                                        AS funds_fi,
        SUM(CASE WHEN vigente THEN 1 ELSE 0 END)       AS funds_fi_vigente
    FROM fondos_inversion
    WHERE administrador IS NOT NULL
    GROUP BY administrador
)
SELECT
    fm.rut,
    COALESCE(fm.nombre, fi.nombre_fi)                  AS nombre,
    COALESCE(fm.funds_fm, 0)                           AS funds_fm,
    COALESCE(fm.funds_fm_vigente, 0)                   AS funds_fm_vigente,
    COALESCE(fi.funds_fi, 0)                           AS funds_fi,
    COALESCE(fi.funds_fi_vigente, 0)                   AS funds_fi_vigente,
    COALESCE(fm.funds_fm, 0) + COALESCE(fi.funds_fi, 0) AS funds_total
FROM fm_admins fm
FULL OUTER JOIN fi_admins fi
    ON LOWER(TRIM(fm.nombre)) = LOWER(TRIM(fi.nombre_fi))
ORDER BY funds_total DESC
"""


def upgrade() -> None:
    op.execute(_SQL)
    op.execute(f"CREATE UNIQUE INDEX ix_mv_administradores_rut ON {_VIEW} (rut)")
    op.execute(f"CREATE INDEX ix_mv_administradores_nombre ON {_VIEW} (nombre)")


def downgrade() -> None:
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_VIEW}")
