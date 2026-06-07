from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class AportanteFI(Base):
    __tablename__ = "aportantes_fi"
    __table_args__ = (
        UniqueConstraint("run_fondo", "periodo", "rank", name="uq_aportante_fi"),
        Index("ix_aportantes_fi_run_fondo", "run_fondo"),
        Index("ix_aportantes_fi_periodo", "periodo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    periodo: Mapped[date] = mapped_column(Date, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo_persona: Mapped[str | None] = mapped_column(String(1), nullable=True)
    rut: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dv_rut: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pct_propiedad: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class CuotasFI(Base):
    __tablename__ = "cuotas_fi"
    __table_args__ = (
        Index("ix_cuotas_fi_run_fondo", "run_fondo"),
    )

    run_fondo: Mapped[str] = mapped_column(String(20), primary_key=True)
    periodo: Mapped[date] = mapped_column(Date, primary_key=True)
    cuotas_emitidas: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cuotas_pagadas: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cuotas_suscritas_no_pagadas: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_cuotas_promesa: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_contratos_promesa: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_promitentes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valor_libro: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
