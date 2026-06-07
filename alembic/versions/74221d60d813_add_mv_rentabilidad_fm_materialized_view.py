"""add mv_rentabilidad_fm materialized view

Revision ID: 74221d60d813
Revises: 339161ab8607
Create Date: 2026-06-07 17:46:53.647845

Rentability for Mutual Funds (FM) with 1D/1W/1M/1Y/5Y/YTD returns.

Total return = (VC_end * PRODUCT(factor_reparto in period)) / VC_start - 1

- VC = cartola_diaria.valor_cuota (daily NAV per fund/serie)
- factor_reparto is the daily reinvestment multiplier (~1.0001729) applied when
  the fund distributes. Most days it is NaN/NULL/0 — treated as 1.0 (neutral).
- Cumulative product over a period computed as EXP(SUM(LN(factor_reparto)))
  using only valid factors (not NaN, not NULL, > 0).
- Period boundary: factors with start_date < fecha <= ref_date are applied.
- Uses the most-populated date within last 7 days (handles CMF publish lag).
"""
from typing import Sequence, Union

from alembic import op

revision: str = '74221d60d813'
down_revision: Union[str, None] = '339161ab8607'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIEW = "mv_rentabilidad_fm"

_SQL = """
CREATE MATERIALIZED VIEW mv_rentabilidad_fm AS
WITH
-- Reference date: most-populated date within the last 7 days (handles publish lag)
ref AS (
    SELECT fecha AS t
    FROM cartola_diaria
    WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
    GROUP BY fecha
    ORDER BY COUNT(DISTINCT run_fondo) DESC, fecha DESC
    LIMIT 1
),

-- Current NAV per fund/serie on the reference date
current_nav AS (
    SELECT c.run_fondo, c.serie, c.fecha, c.valor_cuota
    FROM cartola_diaria c
    CROSS JOIN ref
    WHERE c.fecha = ref.t
      AND c.valor_cuota IS NOT NULL
      AND c.valor_cuota > 0
),

-- Cumulative reinvestment factor (PRODUCT of factor_reparto) per fund/serie/period.
-- Computed as EXP(SUM(LN(factor))) over valid factors only.
factor_agg AS (
    SELECT
        cd.run_fondo,
        cd.serie,
        EXP(SUM(CASE WHEN cd.fecha >  (SELECT t FROM ref) - 1    THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1d,
        EXP(SUM(CASE WHEN cd.fecha >  (SELECT t FROM ref) - 7    THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1w,
        EXP(SUM(CASE WHEN cd.fecha >  (SELECT t FROM ref) - 30   THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1m,
        EXP(SUM(CASE WHEN cd.fecha >  (SELECT t FROM ref) - 365  THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1y,
        EXP(SUM(CASE WHEN cd.fecha >  (SELECT t FROM ref) - 1825 THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_5y,
        EXP(SUM(CASE WHEN cd.fecha >= DATE_TRUNC('year', (SELECT t FROM ref))::date
                                                                THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_ytd
    FROM cartola_diaria cd
    CROSS JOIN ref
    WHERE cd.fecha >  ref.t - 1825
      AND cd.fecha <= ref.t
      AND cd.factor_reparto IS NOT NULL
      AND cd.factor_reparto != 'NaN'::numeric
      AND cd.factor_reparto > 0
    GROUP BY cd.run_fondo, cd.serie
)

SELECT
    c.run_fondo,
    c.serie,
    c.fecha                                AS fecha_calculo,
    c.valor_cuota                          AS valor_actual,
    fm.nombre_fondo,
    fm.razon_social_administradora         AS administrador,

    -- 1D
    CASE WHEN v1d.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1d, 1) / v1d.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_1d,

    -- 1W
    CASE WHEN v1w.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1w, 1) / v1w.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_1w,

    -- 1M
    CASE WHEN v1m.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1m, 1) / v1m.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_1m,

    -- 1Y
    CASE WHEN v1y.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1y, 1) / v1y.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_1y,

    -- 5Y
    CASE WHEN v5y.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_5y, 1) / v5y.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_5y,

    -- YTD
    CASE WHEN vytd.valor_cuota > 0
         THEN ROUND(((c.valor_cuota * COALESCE(fa.f_ytd, 1) / vytd.valor_cuota - 1) * 100)::numeric, 4)
    END AS r_ytd

FROM current_nav c
LEFT JOIN fondo_mutuo fm ON fm.run_fondo = c.run_fondo

LEFT JOIN cartola_diaria v1d  ON v1d.run_fondo  = c.run_fondo AND v1d.serie  = c.serie
                              AND v1d.fecha  = c.fecha - 1
LEFT JOIN cartola_diaria v1w  ON v1w.run_fondo  = c.run_fondo AND v1w.serie  = c.serie
                              AND v1w.fecha  = c.fecha - 7
LEFT JOIN cartola_diaria v1m  ON v1m.run_fondo  = c.run_fondo AND v1m.serie  = c.serie
                              AND v1m.fecha  = c.fecha - 30
LEFT JOIN cartola_diaria v1y  ON v1y.run_fondo  = c.run_fondo AND v1y.serie  = c.serie
                              AND v1y.fecha  = c.fecha - 365
LEFT JOIN cartola_diaria v5y  ON v5y.run_fondo  = c.run_fondo AND v5y.serie  = c.serie
                              AND v5y.fecha  = c.fecha - 1825
LEFT JOIN cartola_diaria vytd ON vytd.run_fondo = c.run_fondo AND vytd.serie = c.serie
                              AND vytd.fecha = (DATE_TRUNC('year', c.fecha) - INTERVAL '1 day')::date

LEFT JOIN factor_agg fa ON fa.run_fondo = c.run_fondo AND fa.serie = c.serie
"""


def upgrade() -> None:
    op.execute(_SQL)
    op.execute("CREATE UNIQUE INDEX ix_mv_rentabilidad_fm_pk  ON mv_rentabilidad_fm (run_fondo, serie)")
    op.execute("CREATE INDEX ix_mv_rentabilidad_fm_r1m ON mv_rentabilidad_fm (r_1m) WHERE r_1m IS NOT NULL")
    op.execute("CREATE INDEX ix_mv_rentabilidad_fm_r1y ON mv_rentabilidad_fm (r_1y) WHERE r_1y IS NOT NULL")


def downgrade() -> None:
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_VIEW}")
