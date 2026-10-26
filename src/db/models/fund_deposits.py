from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKeyConstraint, Integer, Text, text, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.db.engine import Base


class FundDeposit(Base):
    __tablename__ = 'fund_deposits'
    registration: Mapped[str] = mapped_column(Text,primary_key=True)
    run: Mapped[str] = mapped_column(Text,primary_key=True)
    fund_name: Mapped[str] = mapped_column(Text)
    administrator: Mapped[str] = mapped_column(Text)
    deposit_date: Mapped[date] = mapped_column(Date)
    modified_date: Mapped[date] = mapped_column(Date)
    fund_type: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
    liquidated: Mapped[bool] = mapped_column(Boolean)
    documents: Mapped[list] = mapped_column(JSONB)
    approval_reference: Mapped[str | None] = mapped_column(Text)
    source_present: Mapped[bool] = mapped_column(Boolean,server_default=text('true'))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=text('now()'))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=text('now()'))

    __table_args__ = (
        CheckConstraint("fund_type IN ('FM','FIR','FNR')"),
        Index('ix_fund_deposits_deposit',text('deposit_date DESC'),'registration','run'),
        Index('ix_fund_deposits_modified',text('modified_date DESC'),'registration','run'),
        Index('ix_fund_deposits_admin','administrator'),Index('ix_fund_deposits_type','fund_type'),
    )


class FundDepositImport(Base):
    __tablename__ = 'fund_deposit_imports'
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=text('now()'))
    row_count: Mapped[int] = mapped_column(Integer)
    inserted: Mapped[int] = mapped_column(Integer)
    updated: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(Text)


class FundDepositRevision(Base):
    __tablename__ = 'fund_deposit_revisions'
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    registration: Mapped[str] = mapped_column(Text)
    run: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (ForeignKeyConstraint(['registration','run'],['fund_deposits.registration','fund_deposits.run']),
                      Index('ix_fund_deposit_revisions_identity','registration','run',text('observed_at DESC')))
