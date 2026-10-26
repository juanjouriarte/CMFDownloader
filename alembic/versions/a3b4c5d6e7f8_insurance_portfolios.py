"""Insurance source archives, atomic monthly imports, files and parsed records."""
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS insurance_archives (
        sha256 TEXT PRIMARY KEY, content BYTEA NOT NULL, source_url TEXT NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL, size_bytes BIGINT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS insurance_imports (
        id BIGSERIAL PRIMARY KEY, entity_group TEXT NOT NULL CHECK (entity_group IN ('CSVID','CSGEN')),
        period DATE NOT NULL CHECK (EXTRACT(day FROM period)=1),
        archive_sha256 TEXT NOT NULL REFERENCES insurance_archives(sha256),
        parser_version TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT false,
        loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        accepted_records INTEGER NOT NULL, report JSONB NOT NULL)""")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_insurance_active_month
        ON insurance_imports(entity_group,period) WHERE active""")
    op.execute("""CREATE TABLE IF NOT EXISTS insurance_files (
        id BIGSERIAL PRIMARY KEY, import_id BIGINT NOT NULL REFERENCES insurance_imports(id),
        filename TEXT NOT NULL, family TEXT NOT NULL, insurer_rut TEXT NOT NULL, insurer_name TEXT,
        status TEXT NOT NULL CHECK (status IN ('validated','accepted_with_warning','partially_reconciled','quarantined')),
        sha256 TEXT NOT NULL, accepted_rows INTEGER NOT NULL DEFAULT 0,
        source_rows INTEGER, declared_records INTEGER, actual_records INTEGER,
        reason TEXT, evidence JSONB NOT NULL, UNIQUE(import_id,filename))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_insurance_files_insurer ON insurance_files(import_id,insurer_rut)")
    op.execute("""CREATE TABLE IF NOT EXISTS insurance_records (
        id BIGSERIAL PRIMARY KEY, import_id BIGINT NOT NULL REFERENCES insurance_imports(id),
        file_id BIGINT NOT NULL REFERENCES insurance_files(id), insurer_rut TEXT NOT NULL,
        family TEXT NOT NULL, record_type TEXT NOT NULL, record_role TEXT NOT NULL,
        asset_class TEXT, aggregation_role TEXT NOT NULL CHECK (aggregation_role IN ('primary','complementary','contract','auxiliary')),
        instrument_type TEXT, identifier TEXT, display_name TEXT NOT NULL, issuer_rut TEXT, issuer_name TEXT,
        administrator_rut TEXT, administrator_name TEXT, fund_kind TEXT, fund_run TEXT, series TEXT,
        currency TEXT, currency_raw TEXT, country TEXT, rating TEXT, maturity_date DATE,
        purchase_yield NUMERIC, market_yield NUMERIC, units NUMERIC, nominal NUMERIC, final_value_clp NUMERIC,
        source_line INTEGER NOT NULL, source_sha256 TEXT NOT NULL,
        data_quality TEXT NOT NULL, quality_flags JSONB NOT NULL, fields JSONB NOT NULL,
        UNIQUE(file_id,source_line))""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_insurance_records_filter
        ON insurance_records(import_id,aggregation_role,asset_class,insurer_rut)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_insurance_records_admin
        ON insurance_records(import_id,administrator_rut)""")
    op.execute("""CREATE INDEX IF NOT EXISTS ix_insurance_records_value
        ON insurance_records(import_id,final_value_clp DESC NULLS LAST,id)""")


def downgrade():
    for table in ("insurance_records", "insurance_files", "insurance_imports", "insurance_archives"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
