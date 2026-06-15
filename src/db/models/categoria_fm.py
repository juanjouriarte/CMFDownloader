from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class CategoriaFM(Base):
    __tablename__ = "categoria_fm"
    __table_args__ = (
        Index("ix_categoria_fm_periodo", "periodo"),
        Index("ix_categoria_fm_categoria", "categoria"),
    )

    run_fondo: Mapped[str] = mapped_column(String(20), primary_key=True)
    periodo: Mapped[date] = mapped_column(Date, primary_key=True)
    categoria: Mapped[str] = mapped_column(String(30), nullable=False)
    grupo: Mapped[str] = mapped_column(String(40), nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    nombre_cat: Mapped[str] = mapped_column(String(120), nullable=False)
    confianza: Mapped[str] = mapped_column(String(10), nullable=False)
    pct_equity: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    pct_naci: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    pct_uf: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    pct_clp: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    wam_dias: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
