"""Daily NNM facts, independent of current classifications and fund activity.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
"""
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None

FM_SQL = """
WITH observations AS (
 SELECT cd.run_fondo,cd.fecha,COALESCE(CASE UPPER(TRIM(cd.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(cd.moneda)) END, CASE UPPER(TRIM(f.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(f.moneda)) END) currency,
   cd.patrimonio_neto IS NOT NULL AND cd.patrimonio_neto>=0
     AND cd.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity') has_nav,
   CASE WHEN cd.monto_aportado::text NOT IN ('NaN','Infinity','-Infinity')
     AND cd.monto_rescatado::text NOT IN ('NaN','Infinity','-Infinity')
     AND cd.monto_aportado>=0 AND cd.monto_rescatado>=0 THEN cd.monto_aportado END aportes,
   CASE WHEN cd.monto_aportado::text NOT IN ('NaN','Infinity','-Infinity')
     AND cd.monto_rescatado::text NOT IN ('NaN','Infinity','-Infinity')
     AND cd.monto_aportado>=0 AND cd.monto_rescatado>=0 THEN cd.monto_rescatado END rescates
 FROM cartola_diaria cd JOIN fondo_mutuo f USING(run_fondo)
 WHERE cd.fecha>=DATE '2020-01-01'
)
SELECT run_fondo,fecha,currency,BOOL_OR(has_nav) has_nav,
 SUM(aportes) aportes,SUM(rescates) rescates,SUM(aportes-rescates) reported,
 COUNT(*) observations,COUNT(aportes) reported_observations
FROM observations GROUP BY run_fondo,fecha,currency
"""

FI_SQL = """
WITH
      source AS MATERIALIZED (
        SELECT v.run_fondo, v.serie, v.fecha, CASE UPPER(TRIM(v.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(v.moneda)) END currency,
          v.valor_libro price,
          COALESCE(v.valor_libro=0 AND v.patrimonio_neto=0, FALSE) empty_series,
          CASE WHEN v.valor_libro>0 AND v.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
            AND v.patrimonio_neto>=0 THEN v.patrimonio_neto::numeric/v.valor_libro END units,
          v.patrimonio_neto IS NOT NULL AND v.patrimonio_neto>=0 has_nav
        FROM valores_cuota_fi v
        WHERE v.fecha >= DATE '2019-12-25'
      ), paired AS (
        SELECT *, LAG(fecha) OVER w previous_date, LAG(currency) OVER w previous_currency,
          LAG(units) OVER w previous_units, LAG(empty_series) OVER w previous_empty_series
        FROM source WINDOW w AS (PARTITION BY run_fondo,serie ORDER BY fecha)
      ), comparable AS (
        SELECT *, COALESCE(empty_series AND previous_empty_series, FALSE) unchanged_empty
        FROM paired
      ), daily AS (
        SELECT run_fondo,fecha,currency,
          ARRAY_AGG(serie ORDER BY serie) series,
          BOOL_OR(has_nav) has_nav,
          BOOL_AND((unchanged_empty OR (units IS NOT NULL AND previous_units IS NOT NULL))
            AND currency IS NOT DISTINCT FROM previous_currency) valid_values,
          MIN(previous_date) earliest_base, MAX(previous_date) latest_base,
          SUM(CASE WHEN unchanged_empty THEN 0 ELSE (units-previous_units)*price END) estimate
        FROM comparable GROUP BY run_fondo,fecha,currency
      ), snapshots AS (
        SELECT *, LAG(fecha) OVER w previous_snapshot, LAG(series) OVER w previous_series
        FROM daily WINDOW w AS (PARTITION BY run_fondo,currency ORDER BY fecha)
      ), checked AS (
        SELECT *, CASE
          WHEN previous_snapshot IS NULL THEN 'missing_base'
          WHEN fecha-previous_snapshot>7 THEN 'gap'
          WHEN series IS DISTINCT FROM previous_series THEN 'series_change'
          WHEN NOT valid_values OR earliest_base IS DISTINCT FROM previous_snapshot
            OR latest_base IS DISTINCT FROM previous_snapshot THEN 'incomparable_nav'
          END exclusion
        FROM snapshots
      )
SELECT run_fondo,fecha,currency,has_nav,previous_snapshot,estimate,exclusion FROM checked
"""

def upgrade():
    for kind, query in (("fm", FM_SQL), ("fi", FI_SQL)):
        op.execute(f"CREATE MATERIALIZED VIEW IF NOT EXISTS mv_nnm_daily_{kind} AS {query}")
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ix_nnm_daily_{kind}_key "
                   f"ON mv_nnm_daily_{kind} (run_fondo,fecha,currency)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_nnm_daily_{kind}_currency_date "
                   f"ON mv_nnm_daily_{kind} (currency,fecha)")
        op.execute(f"ANALYZE mv_nnm_daily_{kind}")


def downgrade():
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_nnm_daily_fi")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_nnm_daily_fm")
