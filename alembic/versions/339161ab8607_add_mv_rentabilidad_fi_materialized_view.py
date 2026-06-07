"""add mv_rentabilidad_fi materialized view

Revision ID: 339161ab8607
Revises: 5880f3682e10
Create Date: 2026-06-07 16:50:29.106321

Rentability for rescatable FI funds (open-end investment funds).

Formula per period:
  r = (VL_end - VL_start + SUM(dividends with fec_lim in period)) / VL_start × 100

- VL = valores_cuota_fi.valor_libro (daily NAV, published every calendar day)
- Uses the most-populated recent date (within last 7 days) — maximises fund coverage
  regardless of CMF publish lag. Falls back to absolute MAX if only one date has data.
- Dividends: dividendos table via nemotecnicos_fi, CLP only (moneda = '$')
- fec_lim = ex-dividend date; included in the period that ends on or after that date
- Only rescatable = TRUE funds included
"""
from typing import Sequence, Union

from alembic import op

revision: str = '339161ab8607'
down_revision: Union[str, None] = '5880f3682e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIEW = "mv_rentabilidad_fi"

_SQL = """
CREATE MATERIALIZED VIEW mv_rentabilidad_fi AS
WITH
-- Reference date: most-populated date within the last 7 days.
-- Handles CMF publish lag — most funds may be 1-2 days behind the absolute max.
-- Tie-break: prefer the more recent date.
ref AS (
    SELECT v.fecha AS t
    FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    WHERE f.rescatable = TRUE
      AND v.fecha >= (SELECT MAX(fecha) FROM valores_cuota_fi) - 7
    GROUP BY v.fecha
    ORDER BY COUNT(DISTINCT v.run_fondo) DESC, v.fecha DESC
    LIMIT 1
),

-- NAV for rescatable funds on the reference date
current_nav AS (
    SELECT v.run_fondo, v.serie, v.fecha, v.valor_libro
    FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    CROSS JOIN ref
    WHERE v.fecha = ref.t
      AND f.rescatable = TRUE
      AND v.valor_libro IS NOT NULL
      AND v.valor_libro > 0
),

-- CLP dividends per fund/serie via nemotecnicos_fi
divs AS (
    SELECT
        n.run_fondo,
        n.serie,
        d.fec_lim::date AS fec_lim,
        d.val_acc
    FROM dividendos d
    JOIN nemotecnicos_fi n ON n.nemotecnico = d.nemo
    WHERE d.val_acc > 0
      AND d.moneda = '$'
      AND d.fec_lim IS NOT NULL
),

-- Pre-aggregate dividends per fund/serie relative to the global ref date
div_agg AS (
    SELECT
        d.run_fondo,
        d.serie,
        SUM(CASE WHEN d.fec_lim = (SELECT t FROM ref)                            THEN d.val_acc ELSE 0 END) AS d_1d,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 7                       THEN d.val_acc ELSE 0 END) AS d_1w,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 30                      THEN d.val_acc ELSE 0 END) AS d_1m,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 365                     THEN d.val_acc ELSE 0 END) AS d_1y,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 1825                    THEN d.val_acc ELSE 0 END) AS d_5y,
        SUM(CASE WHEN d.fec_lim >= DATE_TRUNC('year', (SELECT t FROM ref))::date  THEN d.val_acc ELSE 0 END) AS d_ytd
    FROM divs d
    GROUP BY d.run_fondo, d.serie
)

SELECT
    c.run_fondo,
    c.serie,
    c.fecha                                AS fecha_calculo,
    c.valor_libro                          AS valor_actual,
    f.razon_social,
    f.administrador,

    -- 1D
    CASE WHEN v1d.valor_libro > 0
         THEN ROUND(((c.valor_libro - v1d.valor_libro + COALESCE(da.d_1d, 0))
                     / v1d.valor_libro * 100)::numeric, 4)
    END AS r_1d,

    -- 1W
    CASE WHEN v1w.valor_libro > 0
         THEN ROUND(((c.valor_libro - v1w.valor_libro + COALESCE(da.d_1w, 0))
                     / v1w.valor_libro * 100)::numeric, 4)
    END AS r_1w,

    -- 1M
    CASE WHEN v1m.valor_libro > 0
         THEN ROUND(((c.valor_libro - v1m.valor_libro + COALESCE(da.d_1m, 0))
                     / v1m.valor_libro * 100)::numeric, 4)
    END AS r_1m,

    -- 1Y
    CASE WHEN v1y.valor_libro > 0
         THEN ROUND(((c.valor_libro - v1y.valor_libro + COALESCE(da.d_1y, 0))
                     / v1y.valor_libro * 100)::numeric, 4)
    END AS r_1y,

    -- 5Y
    CASE WHEN v5y.valor_libro > 0
         THEN ROUND(((c.valor_libro - v5y.valor_libro + COALESCE(da.d_5y, 0))
                     / v5y.valor_libro * 100)::numeric, 4)
    END AS r_5y,

    -- YTD
    CASE WHEN vytd.valor_libro > 0
         THEN ROUND(((c.valor_libro - vytd.valor_libro + COALESCE(da.d_ytd, 0))
                     / vytd.valor_libro * 100)::numeric, 4)
    END AS r_ytd

FROM current_nav c
JOIN fondos_inversion f ON f.run_fondo = c.run_fondo

LEFT JOIN valores_cuota_fi v1d  ON v1d.run_fondo  = c.run_fondo AND v1d.serie  = c.serie
                                AND v1d.fecha  = c.fecha - 1
LEFT JOIN valores_cuota_fi v1w  ON v1w.run_fondo  = c.run_fondo AND v1w.serie  = c.serie
                                AND v1w.fecha  = c.fecha - 7
LEFT JOIN valores_cuota_fi v1m  ON v1m.run_fondo  = c.run_fondo AND v1m.serie  = c.serie
                                AND v1m.fecha  = c.fecha - 30
LEFT JOIN valores_cuota_fi v1y  ON v1y.run_fondo  = c.run_fondo AND v1y.serie  = c.serie
                                AND v1y.fecha  = c.fecha - 365
LEFT JOIN valores_cuota_fi v5y  ON v5y.run_fondo  = c.run_fondo AND v5y.serie  = c.serie
                                AND v5y.fecha  = c.fecha - 1825
LEFT JOIN valores_cuota_fi vytd ON vytd.run_fondo = c.run_fondo AND vytd.serie = c.serie
                                AND vytd.fecha = (DATE_TRUNC('year', c.fecha) - INTERVAL '1 day')::date

LEFT JOIN div_agg da ON da.run_fondo = c.run_fondo AND da.serie = c.serie
"""


def upgrade() -> None:
    op.execute(_SQL)
    # Unique index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
    op.execute("CREATE UNIQUE INDEX ix_mv_rentabilidad_fi_pk   ON mv_rentabilidad_fi (run_fondo, serie)")
    # Ranking/sorting indexes for the downstream app
    op.execute("CREATE INDEX ix_mv_rentabilidad_fi_r1m ON mv_rentabilidad_fi (r_1m) WHERE r_1m IS NOT NULL")
    op.execute("CREATE INDEX ix_mv_rentabilidad_fi_r1y ON mv_rentabilidad_fi (r_1y) WHERE r_1y IS NOT NULL")


def downgrade() -> None:
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_VIEW}")
