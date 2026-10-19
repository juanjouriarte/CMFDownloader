"""CMF Article 12 disclosures, source windows and code definitions.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op

revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE IF NOT EXISTS insider_codes (
        domain VARCHAR(30) NOT NULL, code VARCHAR(20) NOT NULL,
        label TEXT NOT NULL, source_url TEXT NOT NULL, PRIMARY KEY(domain,code))''')
    op.execute('''CREATE TABLE IF NOT EXISTS insider_imports (
        id BIGSERIAL PRIMARY KEY, date_from DATE NOT NULL, date_to DATE NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(), row_count INTEGER NOT NULL,
        sources JSONB NOT NULL,
        CHECK(date_from<=date_to), CHECK(row_count>=0))''')
    op.execute('''CREATE INDEX IF NOT EXISTS ix_insider_imports_window
        ON insider_imports(date_from,date_to)''')
    op.execute('''CREATE TABLE IF NOT EXISTS insider_transactions (
        id BIGSERIAL PRIMARY KEY, fingerprint VARCHAR(64) NOT NULL, occurrence INTEGER NOT NULL,
        company_rut VARCHAR(20) NOT NULL, company_name TEXT NOT NULL,
        entity_status VARCHAR(2) NOT NULL, relationship_code VARCHAR(20) NOT NULL,
        informant_rut VARCHAR(20), informant_dv VARCHAR(2), paternal_name TEXT, maternal_name TEXT,
        informant_name TEXT, transaction_date DATE NOT NULL, communication_date DATE,
        transaction_type VARCHAR(20) NOT NULL, other_transaction_type TEXT,
        security_type VARCHAR(20) NOT NULL, other_security_type TEXT,
        own_company_shares BOOLEAN, ticker TEXT, instrument_issuer_rut VARCHAR(20),
        foreign_issuer BOOLEAN, instrument_issuer_name TEXT, series TEXT,
        units NUMERIC, unit_price_or_rate NUMERIC, amount_clp NUMERIC,
        on_exchange BOOLEAN, broker_rut VARCHAR(20), control_intent BOOLEAN,
        operation_pct NUMERIC, final_holding_pct NUMERIC, observations TEXT,
        is_amendment BOOLEAN NOT NULL, amendment_reference VARCHAR(40), raw_cells JSONB NOT NULL,
        is_current BOOLEAN NOT NULL DEFAULT true,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_import_id BIGINT NOT NULL REFERENCES insider_imports(id),
        CONSTRAINT uq_insider_fingerprint_occurrence UNIQUE(fingerprint,occurrence),
        CONSTRAINT ck_insider_entity_status CHECK(entity_status IN ('VI','NV')),
        CONSTRAINT ck_insider_occurrence CHECK(occurrence>0))''')
    for name,columns,where in [
        ('current_date','transaction_date',' WHERE is_current'),
        ('company_date','company_rut,transaction_date',''),
        ('informant_date','informant_rut,transaction_date',''),
        ('ticker_date','ticker,transaction_date',''),
    ]:
        op.execute(f'CREATE INDEX IF NOT EXISTS ix_insider_{name} ON insider_transactions({columns}){where}')
    # Seeded by the downloader from the versioned Article 12 code catalog.


def downgrade():
    op.execute('DROP TABLE IF EXISTS insider_transactions')
    op.execute('DROP TABLE IF EXISTS insider_imports')
    op.execute('DROP TABLE IF EXISTS insider_codes')
