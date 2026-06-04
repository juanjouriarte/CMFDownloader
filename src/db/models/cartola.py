from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class CartolaDiaria(Base):
    """Cartola diaria de Fondos Mutuos — fuente: CMF cfm_download.php"""

    __tablename__ = "cartola_diaria"
    __table_args__ = (UniqueConstraint("fecha", "run_fondo", name="uq_cartola_fecha_fondo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    nombre_fondo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rut_administradora: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nombre_administradora: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valor_cuota: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    patrimonio: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    numero_cuotas: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    participes: Mapped[int | None] = mapped_column(Integer, nullable=True)
