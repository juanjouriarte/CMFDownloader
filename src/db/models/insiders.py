from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import ForeignKey, BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, Index, CheckConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.db.engine import Base


class InsiderCode(Base):
    __tablename__ = 'insider_codes'
    domain: Mapped[str] = mapped_column(String(30), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)


class InsiderImport(Base):
    """A successful, atomic replacement of both VI and NV for a date window."""
    __tablename__ = 'insider_imports'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    row_count: Mapped[int] = mapped_column(Integer)
    sources: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (Index('ix_insider_imports_window', 'date_from', 'date_to'),)


class InsiderTransaction(Base):
    """A source disclosure, not necessarily a unique economic transaction.

    CMF does not expose a stable ID for unamended rows. Full-row fingerprints
    plus occurrence preserve repeated identical rows in a source snapshot.
    Superseded rows remain in the table with is_current=False for audit.
    """
    __tablename__ = 'insider_transactions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    occurrence: Mapped[int] = mapped_column(Integer)
    company_rut: Mapped[str] = mapped_column(String(20))
    company_name: Mapped[str] = mapped_column(Text)
    entity_status: Mapped[str] = mapped_column(String(2))
    relationship_code: Mapped[str] = mapped_column(String(20))
    informant_rut: Mapped[str | None] = mapped_column(String(20))
    informant_dv: Mapped[str | None] = mapped_column(String(2))
    paternal_name: Mapped[str | None] = mapped_column(Text)
    maternal_name: Mapped[str | None] = mapped_column(Text)
    informant_name: Mapped[str | None] = mapped_column(Text)
    transaction_date: Mapped[date] = mapped_column(Date)
    communication_date: Mapped[date | None] = mapped_column(Date)
    transaction_type: Mapped[str] = mapped_column(String(20))
    other_transaction_type: Mapped[str | None] = mapped_column(Text)
    security_type: Mapped[str] = mapped_column(String(20))
    other_security_type: Mapped[str | None] = mapped_column(Text)
    own_company_shares: Mapped[bool | None] = mapped_column(Boolean)
    ticker: Mapped[str | None] = mapped_column(Text)
    instrument_issuer_rut: Mapped[str | None] = mapped_column(String(20))
    foreign_issuer: Mapped[bool | None] = mapped_column(Boolean)
    instrument_issuer_name: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(Text)
    units: Mapped[Decimal | None] = mapped_column(Numeric)
    unit_price_or_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    amount_clp: Mapped[Decimal | None] = mapped_column(Numeric)
    on_exchange: Mapped[bool | None] = mapped_column(Boolean)
    broker_rut: Mapped[str | None] = mapped_column(String(20))
    control_intent: Mapped[bool | None] = mapped_column(Boolean)
    operation_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    final_holding_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    observations: Mapped[str | None] = mapped_column(Text)
    is_amendment: Mapped[bool] = mapped_column(Boolean)
    amendment_reference: Mapped[str | None] = mapped_column(String(40))
    source_entities: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    raw_cells: Mapped[list] = mapped_column(JSONB)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text('true'))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    last_import_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("insider_imports.id"))
    __table_args__ = (
        UniqueConstraint('fingerprint', 'occurrence', name='uq_insider_fingerprint_occurrence'),
        CheckConstraint("entity_status IN ('VI','NV')", name='ck_insider_entity_status'),
        CheckConstraint('occurrence > 0', name='ck_insider_occurrence'),
        Index('ix_insider_current_date', 'transaction_date', postgresql_where=text('is_current')),
        Index('ix_insider_company_date', 'company_rut', 'transaction_date'),
        Index('ix_insider_informant_date', 'informant_rut', 'transaction_date'),
        Index('ix_insider_ticker_date', 'ticker', 'transaction_date'),
    )
