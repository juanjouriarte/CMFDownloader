"""Historical Banco Central FX observations and derived CLP rates."""
from alembic import op

revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE IF NOT EXISTS fx_series (
        currency TEXT PRIMARY KEY, series_code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, unit_kind TEXT NOT NULL
          CHECK(unit_kind IN ('clp_per_unit','per_usd')),
        checked_through DATE, imported_at TIMESTAMPTZ)''')
    op.execute('''CREATE TABLE IF NOT EXISTS fx_observations (
        series_code TEXT NOT NULL REFERENCES fx_series(series_code),
        date DATE NOT NULL, value NUMERIC(32,12), status TEXT NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(series_code,date),
        CHECK(value IS NULL OR value > 0))''')
    op.execute('''CREATE OR REPLACE VIEW fx_daily AS
        SELECT s.currency,o.date,
          CASE WHEN s.unit_kind='clp_per_unit' THEN o.value
               ELSE usd.value / NULLIF(o.value,0) END AS clp_per_unit,
          s.series_code,s.unit_kind,o.value AS source_value,
          CASE WHEN s.unit_kind='per_usd' THEN usd.value END AS usd_clp,
          GREATEST(o.fetched_at,usd.fetched_at) AS fetched_at
        FROM fx_observations o JOIN fx_series s USING(series_code)
        LEFT JOIN fx_observations usd ON usd.series_code='F073.TCO.PRE.Z.D'
          AND usd.date=o.date AND usd.status='OK'
        WHERE o.status='OK' AND o.value>0
          AND (s.unit_kind='clp_per_unit' OR usd.value>0)''')


def downgrade():
    op.execute('DROP VIEW IF EXISTS fx_daily')
    op.execute('DROP TABLE IF EXISTS fx_observations')
    op.execute('DROP TABLE IF EXISTS fx_series')
