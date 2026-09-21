from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class FMFlowAdjustment(Base):
    """Auditable internal migrations excluded from external FM net new money."""

    __tablename__ = "fm_flow_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "source_run_fondo",
            "target_run_fondo",
            "event_date",
            "currency",
            name="uq_fm_flow_adjustment_event",
        ),
        CheckConstraint(
            "status IN ('auto_confirmed', 'confirmed', 'rejected')",
            name="ck_fm_flow_adjustment_status",
        ),
        CheckConstraint("amount > 0", name="ck_fm_flow_adjustment_positive"),
        CheckConstraint(
            "source_run_fondo <> target_run_fondo",
            name="ck_fm_flow_adjustment_distinct_funds",
        ),
        Index(
            "ix_fm_flow_adjustments_target_date",
            "target_run_fondo",
            "event_date",
            "status",
        ),
        Index("ix_fm_flow_adjustments_source", "source_run_fondo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    target_run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(28, 2), nullable=False)
    source_final_aum: Mapped[Decimal] = mapped_column(Numeric(28, 2), nullable=False)
    relative_difference: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    source_category: Mapped[str] = mapped_column(String(30), nullable=False)
    target_category: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'auto_confirmed'")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FMFlowAdjustmentState(Base):
    """Singleton watermark for incremental internal-migration detection."""

    __tablename__ = "fm_flow_adjustment_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_fm_flow_adjustment_state_singleton"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_data_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_full_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
