"""Include active non-rescatable FI with valid NAV in series returns.

Existing additive NAV-plus-distributions methodology and exact period dates remain.
"""
from alembic import op

revision = 'd0e1f2a3b4c5'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None

FI_SQL = """
CREATE MATERIALIZED VIEW mv_rentabilidad_fi AS
WITH ref AS (
    SELECT t FROM (
        SELECT
            v.fecha AS t,
            COUNT(DISTINCT v.run_fondo) AS n,
            MAX(COUNT(DISTINCT v.run_fondo)) OVER () AS max_n
        FROM valores_cuota_fi v
        JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
        WHERE f.vigente IS TRUE
          AND v.fecha <= CURRENT_DATE
          AND v.fecha >= (SELECT MAX(fecha) FROM valores_cuota_fi WHERE fecha<=CURRENT_DATE) - 7
        GROUP BY v.fecha
    ) sub
    WHERE n >= max_n * 0.90
    ORDER BY t DESC
    LIMIT 1
),
current_nav AS (
    SELECT v.run_fondo, v.serie, v.fecha, v.valor_libro,
           CASE UPPER(TRIM(v.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v.moneda)) END AS currency
    FROM valores_cuota_fi v
    JOIN fondos_inversion f ON f.run_fondo = v.run_fondo
    CROSS JOIN ref
    WHERE v.fecha = ref.t
      AND f.vigente IS TRUE
      AND v.valor_libro IS NOT NULL
      AND v.valor_libro > 0
      AND v.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
      AND v.patrimonio_neto >= 0
      AND (CASE UPPER(TRIM(v.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v.moneda)) END) ~ '^(?:[A-Z]{3}|UF)$'
),
tickers AS (
    SELECT DISTINCT run_fondo, serie, REPLACE(nemotecnico::text, '-', '') AS nemo
    FROM nemotecnicos_fi
),
divs AS (
    SELECT c.run_fondo, c.serie, d.fec_lim::date AS fec_lim, d.val_acc
    FROM current_nav c
    JOIN tickers n ON n.run_fondo=c.run_fondo AND n.serie IS NOT DISTINCT FROM c.serie
    JOIN dividendos d ON n.nemo=REPLACE(d.nemo::text, '-', '')
    WHERE d.val_acc > 0
      AND d.val_acc::text NOT IN ('NaN','Infinity','-Infinity')
      AND d.fec_lim IS NOT NULL
      AND d.fec_lim::date <= c.fecha
      AND (CASE UPPER(TRIM(d.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(d.moneda)) END) = c.currency
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
    CASE WHEN v1d.valor_libro > 0 AND v1d.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(v1d.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v1d.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - v1d.valor_libro  + COALESCE(da.d_1d,  0)) / v1d.valor_libro)  * 100, 4) END AS r_1d,
    CASE WHEN v1w.valor_libro > 0 AND v1w.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(v1w.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v1w.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - v1w.valor_libro  + COALESCE(da.d_1w,  0)) / v1w.valor_libro)  * 100, 4) END AS r_1w,
    CASE WHEN v1m.valor_libro > 0 AND v1m.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(v1m.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v1m.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - v1m.valor_libro  + COALESCE(da.d_1m,  0)) / v1m.valor_libro)  * 100, 4) END AS r_1m,
    CASE WHEN v1y.valor_libro > 0 AND v1y.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(v1y.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v1y.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - v1y.valor_libro  + COALESCE(da.d_1y,  0)) / v1y.valor_libro)  * 100, 4) END AS r_1y,
    CASE WHEN v5y.valor_libro > 0 AND v5y.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(v5y.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(v5y.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - v5y.valor_libro  + COALESCE(da.d_5y,  0)) / v5y.valor_libro)  * 100, 4) END AS r_5y,
    CASE WHEN vytd.valor_libro > 0 AND vytd.valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND (CASE UPPER(TRIM(vytd.moneda)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP' WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL ELSE UPPER(TRIM(vytd.moneda)) END) = c.currency THEN ROUND(((c.valor_libro - vytd.valor_libro + COALESCE(da.d_ytd, 0)) / vytd.valor_libro) * 100, 4) END AS r_ytd
FROM current_nav c
JOIN  fondos_inversion f      ON f.run_fondo   = c.run_fondo
LEFT JOIN valores_cuota_fi v1d  ON v1d.run_fondo = c.run_fondo AND v1d.serie IS NOT DISTINCT FROM c.serie AND v1d.fecha = c.fecha - 1
LEFT JOIN valores_cuota_fi v1w  ON v1w.run_fondo = c.run_fondo AND v1w.serie IS NOT DISTINCT FROM c.serie AND v1w.fecha = c.fecha - 7
LEFT JOIN valores_cuota_fi v1m  ON v1m.run_fondo = c.run_fondo AND v1m.serie IS NOT DISTINCT FROM c.serie AND v1m.fecha = c.fecha - 30
LEFT JOIN valores_cuota_fi v1y  ON v1y.run_fondo = c.run_fondo AND v1y.serie IS NOT DISTINCT FROM c.serie AND v1y.fecha = c.fecha - 365
LEFT JOIN valores_cuota_fi v5y  ON v5y.run_fondo = c.run_fondo AND v5y.serie IS NOT DISTINCT FROM c.serie AND v5y.fecha = c.fecha - 1825
LEFT JOIN valores_cuota_fi vytd ON vytd.run_fondo = c.run_fondo AND vytd.serie IS NOT DISTINCT FROM c.serie
    AND vytd.fecha = (DATE_TRUNC('year', c.fecha::timestamp) - INTERVAL '1 day')::date
LEFT JOIN div_agg da            ON da.run_fondo  = c.run_fondo AND da.serie  = c.serie;
"""

PREVIOUS_FI_SQL = """
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

def replace_view(sql):
    op.execute('DROP MATERIALIZED VIEW IF EXISTS mv_rentabilidad_fi')
    op.execute(sql)
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_pk ON mv_rentabilidad_fi (run_fondo, serie)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_r1m ON mv_rentabilidad_fi (r_1m) WHERE r_1m IS NOT NULL')
    op.execute('CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_r1y ON mv_rentabilidad_fi (r_1y) WHERE r_1y IS NOT NULL')


def upgrade():
    replace_view(FI_SQL)


def downgrade():
    replace_view(PREVIOUS_FI_SQL)
