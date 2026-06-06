from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class Tac(Base):
    __tablename__ = "tac"
    __table_args__ = (Index("ix_tac_periodo", "periodo"),)

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    periodo: Mapped[date] = mapped_column(Date, nullable=False)
    run_fondo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    administradora: Mapped[str | None] = mapped_column(Text)
    nombre_fondo: Mapped[str | None] = mapped_column(Text)
    tipo_fondo: Mapped[str | None] = mapped_column(String(5))
    moneda: Mapped[str | None] = mapped_column(String(20))
    serie: Mapped[str | None] = mapped_column(String(20))
    caracteristicas: Mapped[str | None] = mapped_column(Text)
    rem_fija: Mapped[str | None] = mapped_column(Text)
    rem_var: Mapped[str | None] = mapped_column(Text)
    gastos_op: Mapped[str | None] = mapped_column(Text)
    tac_rem_fija: Mapped[float | None] = mapped_column(Numeric(8, 4))
    tac_rem_var: Mapped[float | None] = mapped_column(Numeric(8, 4))
    tac_gastos_op: Mapped[float | None] = mapped_column(Numeric(8, 4))
    tac_total: Mapped[float | None] = mapped_column(Numeric(8, 4))
    cond_colocacion: Mapped[str | None] = mapped_column(Text)
    com_colocacion: Mapped[str | None] = mapped_column(Text)
    cond_diferida: Mapped[str | None] = mapped_column(Text)
    com_diferida: Mapped[str | None] = mapped_column(Text)
