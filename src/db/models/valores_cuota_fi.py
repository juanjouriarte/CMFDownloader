from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class ValorCuotaFI(Base):
    __tablename__ = "valores_cuota_fi"
    __table_args__ = (
        UniqueConstraint("run_fondo", "fecha", "serie", name="uq_valor_cuota_fi"),
        Index("ix_valores_cuota_fi_run_fondo", "run_fondo"),
        Index("ix_valores_cuota_fi_fecha", "fecha"),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    serie: Mapped[str | None] = mapped_column(String(50), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valor_libro: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    valor_economico: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    patrimonio_neto: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    activo_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_aportantes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_aportantes_inst: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agencia: Mapped[str | None] = mapped_column(String(50), nullable=True)
