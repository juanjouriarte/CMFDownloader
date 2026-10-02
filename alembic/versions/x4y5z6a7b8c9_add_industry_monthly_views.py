"""Precompute native-currency monthly industry history.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
"""
from alembic import op

revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None

FM_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_industry_monthly_fm AS
WITH daily AS (
 SELECT cd.run_fondo, cd.fecha AS data_date, COALESCE(CASE UPPER(TRIM(cd.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(cd.moneda)) END, CASE UPPER(TRIM(f.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(f.moneda)) END) AS currency,
        SUM(cd.patrimonio_neto) AS aum, SUM(cd.monto_aportado) AS aportes, SUM(cd.monto_rescatado) AS rescates, SUM(cd.monto_aportado - cd.monto_rescatado) AS reported_nnm
 FROM cartola_diaria cd JOIN fondo_mutuo f USING (run_fondo)
 WHERE cd.fecha <= CURRENT_DATE
 GROUP BY cd.run_fondo, cd.fecha, COALESCE(CASE UPPER(TRIM(cd.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(cd.moneda)) END, CASE UPPER(TRIM(f.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(f.moneda)) END)
)
SELECT run_fondo, DATE_TRUNC('month', data_date)::date AS month, currency,
       MAX(data_date) AS latest_data_date,
       (ARRAY_AGG(aum ORDER BY data_date DESC))[1] AS aum,
       SUM(aportes) AS aportes, SUM(rescates) AS rescates, SUM(reported_nnm) AS nnm
FROM daily GROUP BY run_fondo, DATE_TRUNC('month', data_date)::date, currency
"""

FI_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_industry_monthly_fi AS
WITH daily AS (
 SELECT v.run_fondo, v.fecha AS data_date, COALESCE(CASE UPPER(TRIM(v.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(v.moneda)) END, CASE UPPER(TRIM(f.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(f.moneda)) END) AS currency,
        SUM(v.patrimonio_neto) AS aum, NULL::numeric AS aportes, NULL::numeric AS rescates, NULL::numeric AS reported_nnm
 FROM valores_cuota_fi v JOIN fondos_inversion f USING (run_fondo)
 WHERE v.fecha <= CURRENT_DATE
 GROUP BY v.run_fondo, v.fecha, COALESCE(CASE UPPER(TRIM(v.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(v.moneda)) END, CASE UPPER(TRIM(f.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(f.moneda)) END)
)
SELECT run_fondo, DATE_TRUNC('month', data_date)::date AS month, currency,
       MAX(data_date) AS latest_data_date,
       (ARRAY_AGG(aum ORDER BY data_date DESC))[1] AS aum,
       SUM(aportes) AS aportes, SUM(rescates) AS rescates, SUM(reported_nnm) AS nnm
FROM daily GROUP BY run_fondo, DATE_TRUNC('month', data_date)::date, currency
"""

def upgrade():
    op.execute(FM_SQL)
    op.execute(FI_SQL)
    for kind in ("fm", "fi"):
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ix_industry_monthly_{kind}_key ON mv_industry_monthly_{kind} (run_fondo, month, currency)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_industry_monthly_{kind}_currency_month ON mv_industry_monthly_{kind} (currency, month)")

def downgrade():
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_industry_monthly_fi")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_industry_monthly_fm")
