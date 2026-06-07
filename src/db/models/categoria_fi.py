from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class CategoriaFI(Base):
    __tablename__ = "categoria_fi"
    __table_args__ = (
        Index("ix_categoria_fi_periodo", "periodo"),
        Index("ix_categoria_fi_categoria", "categoria"),
    )

    run_fondo:    Mapped[str]           = mapped_column(String(20), primary_key=True)
    periodo:      Mapped[date]          = mapped_column(Date, primary_key=True)
    categoria:    Mapped[str]           = mapped_column(String(30), nullable=False)
    grupo:        Mapped[str]           = mapped_column(String(40), nullable=False)
    tipo:         Mapped[str]           = mapped_column(String(30), nullable=False)
    nombre_cat:   Mapped[str]           = mapped_column(String(60), nullable=False)
    confianza:    Mapped[str]           = mapped_column(String(10), nullable=False)
    ipsa_ratio:   Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_pe:       Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_inmob:    Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_mh:       Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_eq_nac:   Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_eq_ext:   Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_deuda_nac:Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_deuda_int:Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_fof:      Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    pct_other:    Mapped[float | None]  = mapped_column(Numeric(6, 2), nullable=True)
    met_part:     Mapped[bool | None]   = mapped_column(Boolean, nullable=True)
    updated_at:   Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)
