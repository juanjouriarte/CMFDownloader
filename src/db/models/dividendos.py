from __future__ import annotations

from sqlalchemy import BigInteger, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class Dividendo(Base):
    __tablename__ = "dividendos"
    __table_args__ = (
        UniqueConstraint("nemo", "fec_pago", "descrip_vc", name="uq_dividendo"),
        Index("ix_dividendos_nemo", "nemo"),
        Index("ix_dividendos_fec_pago", "fec_pago"),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    nemo: Mapped[str] = mapped_column(String(30), nullable=False)
    descrip_vc: Mapped[str | None] = mapped_column(Text, nullable=True)
    fec_lim: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fec_pago: Mapped[str | None] = mapped_column(String(20), nullable=True)
    val_acc: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    num_acc_ant: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_acc_der: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_acc_nue: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pre_ant_vc: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_ex_vc: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
