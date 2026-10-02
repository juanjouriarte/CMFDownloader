"""Persistent audited manual category overrides, with effective read views.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
"""
from alembic import op

revision = 'y5z6a7b8c9d0'
down_revision = 'x4y5z6a7b8c9'
branch_labels = None
depends_on = None

COLUMNS = {
 'fm': 'run_fondo periodo categoria grupo tipo nombre_cat confianza pct_equity pct_naci pct_uf pct_clp wam_dias updated_at'.split(),
 'fi': 'run_fondo periodo categoria grupo tipo nombre_cat confianza ipsa_ratio pct_pe pct_inmob pct_mh pct_eq_nac pct_eq_ext pct_deuda_nac pct_deuda_int pct_fof pct_other met_part updated_at'.split(),
}


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS classification_overrides (
        kind VARCHAR(2) NOT NULL CHECK(kind IN ('fm','fi')),
        run_fondo VARCHAR(20) NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        categoria VARCHAR(30) NOT NULL, grupo VARCHAR(80) NOT NULL,
        tipo VARCHAR(80) NOT NULL, nombre_cat VARCHAR(160) NOT NULL,
        reason TEXT NOT NULL CHECK(length(trim(reason))>=8),
        updated_by VARCHAR(120) NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        version INTEGER NOT NULL CHECK(version>0),
        PRIMARY KEY(kind,run_fondo)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS classification_audit (
        id BIGSERIAL PRIMARY KEY,
        kind VARCHAR(2) NOT NULL CHECK(kind IN ('fm','fi')),
        run_fondo VARCHAR(20) NOT NULL,
        action VARCHAR(10) NOT NULL CHECK(action IN ('lock','unlock')),
        actor VARCHAR(120) NOT NULL, reason TEXT NOT NULL,
        before_state JSONB NOT NULL, after_state JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute('CREATE INDEX IF NOT EXISTS ix_classification_audit_fund ON classification_audit(kind,run_fondo,id DESC)')
    for kind, columns in COLUMNS.items():
        fields=[]; synthetic=[]
        for col in columns:
            if col in ('categoria','grupo','tipo','nombre_cat'):
                fields.append(f'COALESCE(o.{col},c.{col}) AS {col}')
                synthetic.append(f'o.{col}')
            elif col=='confianza':
                fields.append("CASE WHEN o.active THEN 'Manual' ELSE c.confianza END AS confianza")
                synthetic.append("'Manual' AS confianza")
            else:
                fields.append(f'c.{col}')
                synthetic.append('o.run_fondo' if col=='run_fondo' else 'o.updated_at::date AS periodo' if col=='periodo'
                                 else 'o.updated_at' if col=='updated_at' else f'NULL AS {col}')
        op.execute(f"""CREATE OR REPLACE VIEW categoria_{kind}_effective AS
            SELECT {','.join(fields)} FROM categoria_{kind} c
            LEFT JOIN classification_overrides o ON o.kind='{kind}' AND o.run_fondo=c.run_fondo AND o.active
            UNION ALL SELECT {','.join(synthetic)} FROM classification_overrides o
            WHERE o.kind='{kind}' AND o.active
              AND NOT EXISTS(SELECT 1 FROM categoria_{kind} c WHERE c.run_fondo=o.run_fondo)""")


def downgrade():
    for kind in COLUMNS:
        op.execute(f'DROP VIEW IF EXISTS categoria_{kind}_effective')
    op.execute('DROP TABLE IF EXISTS classification_audit')
    op.execute('DROP TABLE IF EXISTS classification_overrides')
