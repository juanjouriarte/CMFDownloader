"""mv_rentabilidad: use 90% coverage threshold for ref date selection

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Branch Labels: None
Depends On: None

Previously the ref CTE picked the single most-populated date in the last 7
days, which caused the MV to stay anchored to an older date whenever even one
fund published late on newer days.

New logic: pick the most recent date whose fund count is >= 90% of the maximum
count seen in the 7-day window. This tolerates up to ~10% missing funds while
still preferring recency.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "n3o4p5q6r7s8"
down_revision: Union[str, None] = "m2n3o4p5q6r7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FM_VIEW = "mv_rentabilidad_fm"
_FI_VIEW = "mv_rentabilidad_fi"

_FM_SQL = """
CREATE MATERIALIZED VIEW mv_rentabilidad_fm AS
WITH ref AS (
    SELECT t FROM (
        SELECT
            fecha AS t,
            COUNT(DISTINCT run_fondo) AS n,
            MAX(COUNT(DISTINCT run_fondo)) OVER () AS max_n
        FROM cartola_diaria
        WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
        GROUP BY fecha
    ) sub
    WHERE n >= max_n * 0.90
    ORDER BY t DESC
    LIMIT 1
),
current_nav AS (
    SELECT c.run_fondo, c.serie, c.fecha, c.valor_cuota
    FROM cartola_diaria c
    CROSS JOIN ref
    WHERE c.fecha = ref.t
      AND c.valor_cuota IS NOT NULL
      AND c.valor_cuota > 0
),
factor_agg AS (
    SELECT
        cd.run_fondo,
        cd.serie,
        EXP(SUM(CASE WHEN cd.fecha > (SELECT t FROM ref) - 1    THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1d,
        EXP(SUM(CASE WHEN cd.fecha > (SELECT t FROM ref) - 7    THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1w,
        EXP(SUM(CASE WHEN cd.fecha > (SELECT t FROM ref) - 30   THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1m,
        EXP(SUM(CASE WHEN cd.fecha > (SELECT t FROM ref) - 365  THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_1y,
        EXP(SUM(CASE WHEN cd.fecha > (SELECT t FROM ref) - 1825 THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_5y,
        EXP(SUM(CASE WHEN cd.fecha >= DATE_TRUNC('year', (SELECT t FROM ref)::timestamp)::date
                     THEN LN(cd.factor_reparto) ELSE 0 END)) AS f_ytd
    FROM cartola_diaria cd
    CROSS JOIN ref
    WHERE cd.fecha >  ref.t - 1825
      AND cd.fecha <= ref.t
      AND cd.factor_reparto IS NOT NULL
      AND cd.factor_reparto <> 'NaN'
      AND cd.factor_reparto > 0
    GROUP BY cd.run_fondo, cd.serie
)
SELECT
    c.run_fondo,
    c.serie,
    c.fecha                          AS fecha_calculo,
    c.valor_cuota                    AS valor_actual,
    fm.nombre_fondo,
    fm.razon_social_administradora   AS administrador,
    CASE WHEN v1d.valor_cuota  > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1d,  1) / v1d.valor_cuota)  - 1) * 100, 4) END AS r_1d,
    CASE WHEN v1w.valor_cuota  > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1w,  1) / v1w.valor_cuota)  - 1) * 100, 4) END AS r_1w,
    CASE WHEN v1m.valor_cuota  > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1m,  1) / v1m.valor_cuota)  - 1) * 100, 4) END AS r_1m,
    CASE WHEN v1y.valor_cuota  > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_1y,  1) / v1y.valor_cuota)  - 1) * 100, 4) END AS r_1y,
    CASE WHEN v5y.valor_cuota  > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_5y,  1) / v5y.valor_cuota)  - 1) * 100, 4) END AS r_5y,
    CASE WHEN vytd.valor_cuota > 0 THEN ROUND(((c.valor_cuota * COALESCE(fa.f_ytd, 1) / vytd.valor_cuota) - 1) * 100, 4) END AS r_ytd
FROM current_nav c
LEFT JOIN fondo_mutuo fm     ON fm.run_fondo  = c.run_fondo
LEFT JOIN cartola_diaria v1d  ON v1d.run_fondo = c.run_fondo AND v1d.serie = c.serie AND v1d.fecha = c.fecha - 1
LEFT JOIN cartola_diaria v1w  ON v1w.run_fondo = c.run_fondo AND v1w.serie = c.serie AND v1w.fecha = c.fecha - 7
LEFT JOIN cartola_diaria v1m  ON v1m.run_fondo = c.run_fondo AND v1m.serie = c.serie AND v1m.fecha = c.fecha - 30
LEFT JOIN cartola_diaria v1y  ON v1y.run_fondo = c.run_fondo AND v1y.serie = c.serie AND v1y.fecha = c.fecha - 365
LEFT JOIN cartola_diaria v5y  ON v5y.run_fondo = c.run_fondo AND v5y.serie = c.serie AND v5y.fecha = c.fecha - 1825
LEFT JOIN cartola_diaria vytd ON vytd.run_fondo = c.run_fondo AND vytd.serie = c.serie
    AND vytd.fecha = (DATE_TRUNC('year', c.fecha::timestamp) - INTERVAL '1 day')::date
LEFT JOIN factor_agg fa       ON fa.run_fondo  = c.run_fondo AND fa.serie  = c.serie;
"""

_FI_SQL = """
CREATE MATERIALIZED VIEW mv_rentabilidad_fi AS
WITH ref AS (
    SELECT t FROM (
        SELECT
            v.fecha AS t,
            COUNT(DISTINCT v.run_fondo) AS n,
            MAX(COUNT(DISTINCT v.run_fondo)) OVER () AS max_n
        FROM valores_cuota_fi v
        JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
        WHERE f.rescatable = true
          AND v.fecha >= (SELECT MAX(fecha) FROM valores_cuota_fi) - 7
        GROUP BY v.fecha
    ) sub
    WHERE n >= max_n * 0.90
    ORDER BY t DESC
    LIMIT 1
),
current_nav AS (
    SELECT v.run_fondo, v.serie, v.fecha, v.valor_libro
    FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    CROSS JOIN ref
    WHERE v.fecha = ref.t
      AND f.rescatable = true
      AND v.valor_libro IS NOT NULL
      AND v.valor_libro > 0
),
fund_currency AS (
    SELECT DISTINCT ON (run_fondo, serie) run_fondo, serie, moneda
    FROM valores_cuota_fi
    WHERE moneda IS NOT NULL
    ORDER BY run_fondo, serie, fecha DESC
),
divs AS (
    SELECT n.run_fondo, n.serie, d.fec_lim::date AS fec_lim, d.val_acc
    FROM dividendos d
    JOIN nemotecnicos_fi n  ON REPLACE(n.nemotecnico::text, '-', '') = REPLACE(d.nemo::text, '-', '')
    JOIN fund_currency   fc ON fc.run_fondo = n.run_fondo AND fc.serie = n.serie
    WHERE d.val_acc > 0
      AND d.fec_lim IS NOT NULL
      AND (
            (d.moneda = '$'   AND fc.moneda = '$$'  )
         OR (d.moneda = 'US$' AND fc.moneda = 'PROM')
          )
),
div_agg AS (
    SELECT
        d.run_fondo,
        d.serie,
        SUM(CASE WHEN d.fec_lim =  (SELECT t FROM ref)         THEN d.val_acc ELSE 0 END) AS d_1d,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 7     THEN d.val_acc ELSE 0 END) AS d_1w,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 30    THEN d.val_acc ELSE 0 END) AS d_1m,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 365   THEN d.val_acc ELSE 0 END) AS d_1y,
        SUM(CASE WHEN d.fec_lim >  (SELECT t FROM ref) - 1825  THEN d.val_acc ELSE 0 END) AS d_5y,
        SUM(CASE WHEN d.fec_lim >= DATE_TRUNC('year', (SELECT t FROM ref)::timestamp)::date
                                                               THEN d.val_acc ELSE 0 END) AS d_ytd
    FROM divs d
    GROUP BY d.run_fondo, d.serie
)
SELECT
    c.run_fondo,
    c.serie,
    c.fecha                AS fecha_calculo,
    c.valor_libro          AS valor_actual,
    f.razon_social,
    f.administrador,
    CASE WHEN v1d.valor_libro  > 0 THEN ROUND(((c.valor_libro - v1d.valor_libro  + COALESCE(da.d_1d,  0)) / v1d.valor_libro)  * 100, 4) END AS r_1d,
    CASE WHEN v1w.valor_libro  > 0 THEN ROUND(((c.valor_libro - v1w.valor_libro  + COALESCE(da.d_1w,  0)) / v1w.valor_libro)  * 100, 4) END AS r_1w,
    CASE WHEN v1m.valor_libro  > 0 THEN ROUND(((c.valor_libro - v1m.valor_libro  + COALESCE(da.d_1m,  0)) / v1m.valor_libro)  * 100, 4) END AS r_1m,
    CASE WHEN v1y.valor_libro  > 0 THEN ROUND(((c.valor_libro - v1y.valor_libro  + COALESCE(da.d_1y,  0)) / v1y.valor_libro)  * 100, 4) END AS r_1y,
    CASE WHEN v5y.valor_libro  > 0 THEN ROUND(((c.valor_libro - v5y.valor_libro  + COALESCE(da.d_5y,  0)) / v5y.valor_libro)  * 100, 4) END AS r_5y,
    CASE WHEN vytd.valor_libro > 0 THEN ROUND(((c.valor_libro - vytd.valor_libro + COALESCE(da.d_ytd, 0)) / vytd.valor_libro) * 100, 4) END AS r_ytd
FROM current_nav c
JOIN  fondos_inversion f      ON f.run_fondo   = c.run_fondo
LEFT JOIN valores_cuota_fi v1d  ON v1d.run_fondo = c.run_fondo AND v1d.serie = c.serie AND v1d.fecha = c.fecha - 1
LEFT JOIN valores_cuota_fi v1w  ON v1w.run_fondo = c.run_fondo AND v1w.serie = c.serie AND v1w.fecha = c.fecha - 7
LEFT JOIN valores_cuota_fi v1m  ON v1m.run_fondo = c.run_fondo AND v1m.serie = c.serie AND v1m.fecha = c.fecha - 30
LEFT JOIN valores_cuota_fi v1y  ON v1y.run_fondo = c.run_fondo AND v1y.serie = c.serie AND v1y.fecha = c.fecha - 365
LEFT JOIN valores_cuota_fi v5y  ON v5y.run_fondo = c.run_fondo AND v5y.serie = c.serie AND v5y.fecha = c.fecha - 1825
LEFT JOIN valores_cuota_fi vytd ON vytd.run_fondo = c.run_fondo AND vytd.serie = c.serie
    AND vytd.fecha = (DATE_TRUNC('year', c.fecha::timestamp) - INTERVAL '1 day')::date
LEFT JOIN div_agg da            ON da.run_fondo  = c.run_fondo AND da.serie  = c.serie;
"""

# Original ref CTEs for downgrade
_FM_REF_ORIG = """
    SELECT cartola_diaria.fecha AS t
    FROM cartola_diaria
    WHERE cartola_diaria.fecha >= ((SELECT MAX(fecha) FROM cartola_diaria) - 7)
    GROUP BY cartola_diaria.fecha
    ORDER BY COUNT(DISTINCT cartola_diaria.run_fondo) DESC, cartola_diaria.fecha DESC
    LIMIT 1
"""

_FI_REF_ORIG = """
    SELECT v.fecha AS t
    FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    WHERE f.rescatable = true
      AND v.fecha >= ((SELECT MAX(fecha) FROM valores_cuota_fi) - 7)
    GROUP BY v.fecha
    ORDER BY COUNT(DISTINCT v.run_fondo) DESC, v.fecha DESC
    LIMIT 1
"""


def upgrade() -> None:
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_FM_VIEW}")
    op.execute(_FM_SQL)
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_FI_VIEW}")
    op.execute(_FI_SQL)


def downgrade() -> None:
    # Rebuild with original "max count first" ref logic
    fm_down = _FM_SQL.replace(
        """    SELECT t FROM (
        SELECT
            fecha AS t,
            COUNT(DISTINCT run_fondo) AS n,
            MAX(COUNT(DISTINCT run_fondo)) OVER () AS max_n
        FROM cartola_diaria
        WHERE fecha >= (SELECT MAX(fecha) FROM cartola_diaria) - 7
        GROUP BY fecha
    ) sub
    WHERE n >= max_n * 0.90
    ORDER BY t DESC
    LIMIT 1""",
        _FM_REF_ORIG,
    )
    fi_down = _FI_SQL.replace(
        """    SELECT t FROM (
        SELECT
            v.fecha AS t,
            COUNT(DISTINCT v.run_fondo) AS n,
            MAX(COUNT(DISTINCT v.run_fondo)) OVER () AS max_n
        FROM valores_cuota_fi v
        JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
        WHERE f.rescatable = true
          AND v.fecha >= (SELECT MAX(fecha) FROM valores_cuota_fi) - 7
        GROUP BY v.fecha
    ) sub
    WHERE n >= max_n * 0.90
    ORDER BY t DESC
    LIMIT 1""",
        _FI_REF_ORIG,
    )
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_FM_VIEW}")
    op.execute(fm_down)
    op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_FI_VIEW}")
    op.execute(fi_down)
