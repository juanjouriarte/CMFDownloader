from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class FinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = (
        Index("ix_fs_periodo_rut", "periodo", "rut"),
        Index("ix_fs_rut", "rut"),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    periodo: Mapped[date] = mapped_column(Date, nullable=False)
    rut: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    consolidation_type: Mapped[str | None] = mapped_column(String(1))  # I=Individual, C=Consolidated
    currency: Mapped[str | None] = mapped_column(String(10))
    account: Mapped[str | None] = mapped_column(Text)
    value: Mapped[int | None] = mapped_column(BigInteger)
    tax_type: Mapped[str | None] = mapped_column(String(20))
    statement_type: Mapped[str | None] = mapped_column(String(20))  # ESF C/NC, ERFG, EFMD, etc.
