"""Insurance persistence metadata. Monetary fields are CLP, not native units."""
from sqlalchemy import Table, Column, BigInteger, Integer, Text, Date, DateTime, Boolean, Numeric, LargeBinary, ForeignKey, Index, UniqueConstraint, CheckConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from src.db.engine import Base

archives = Table("insurance_archives", Base.metadata,
    Column("sha256", Text, primary_key=True), Column("content", LargeBinary, nullable=False),
    Column("source_url", Text, nullable=False), Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("size_bytes", BigInteger, nullable=False))
imports = Table("insurance_imports", Base.metadata,
    Column("id", BigInteger, primary_key=True), Column("entity_group", Text, nullable=False),
    Column("period", Date, nullable=False), Column("archive_sha256", Text, ForeignKey("insurance_archives.sha256"), nullable=False),
    Column("parser_version", Text, nullable=False), Column("active", Boolean, nullable=False, server_default=text("false")),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("checked_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("accepted_records", Integer, nullable=False), Column("report", JSONB, nullable=False),
    CheckConstraint("entity_group IN ('CSVID','CSGEN')", name="insurance_imports_entity_group_check"),
    CheckConstraint("EXTRACT(day FROM period)=1", name="insurance_imports_period_check"))
files = Table("insurance_files", Base.metadata,
    Column("id", BigInteger, primary_key=True), Column("import_id", BigInteger, ForeignKey("insurance_imports.id"), nullable=False),
    *[Column(name, Text, nullable=False) for name in ("filename", "family", "insurer_rut", "status", "sha256")],
    Column("insurer_name", Text), Column("accepted_rows", Integer, nullable=False, server_default=text("0")),
    *[Column(name, Integer) for name in ("source_rows", "declared_records", "actual_records")],
    Column("reason", Text), Column("evidence", JSONB, nullable=False),
    UniqueConstraint("import_id", "filename", name="insurance_files_import_id_filename_key"),
    CheckConstraint("status IN ('validated','accepted_with_warning','partially_reconciled','quarantined')",
                    name="insurance_files_status_check"))
records = Table("insurance_records", Base.metadata,
    Column("id", BigInteger, primary_key=True), Column("import_id", BigInteger, ForeignKey("insurance_imports.id"), nullable=False),
    Column("file_id", BigInteger, ForeignKey("insurance_files.id"), nullable=False),
    *[Column(name, Text, nullable=False) for name in ("insurer_rut", "family", "record_type", "record_role", "aggregation_role", "display_name", "source_sha256", "data_quality")],
    *[Column(name, Text) for name in ("asset_class", "instrument_type", "identifier", "issuer_rut", "issuer_name",
        "administrator_rut", "administrator_name", "fund_kind", "fund_run", "series", "currency", "currency_raw", "country", "rating")],
    Column("maturity_date", Date),
    *[Column(name, Numeric) for name in ("purchase_yield", "market_yield", "units", "nominal", "final_value_clp")],
    Column("source_line", Integer, nullable=False), Column("quality_flags", JSONB, nullable=False),
    Column("fields", JSONB, nullable=False),
    UniqueConstraint("file_id", "source_line", name="insurance_records_file_id_source_line_key"),
    CheckConstraint("aggregation_role IN ('primary','complementary','contract','auxiliary')",
                    name="insurance_records_aggregation_role_check"))

Index("uq_insurance_active_month", imports.c.entity_group, imports.c.period,
      unique=True, postgresql_where=imports.c.active)
Index("ix_insurance_files_insurer", files.c.import_id, files.c.insurer_rut)
Index("ix_insurance_records_filter", records.c.import_id, records.c.aggregation_role,
      records.c.asset_class, records.c.insurer_rut)
Index("ix_insurance_records_admin", records.c.import_id, records.c.administrator_rut)
Index("ix_insurance_records_value", records.c.import_id,
      records.c.final_value_clp.desc().nulls_last(), records.c.id)
