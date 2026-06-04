from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class CartolaDiaria(Base):
    """Cartola diaria de Fondos Mutuos — fuente: CMF cfm_download.php"""

    __tablename__ = "cartola_diaria"
    __table_args__ = (UniqueConstraint("fecha", "run_fondo", "serie", name="uq_cartola_fecha_fondo_serie"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identifiers
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    serie: Mapped[str | None] = mapped_column(String(20), nullable=True)
    run_adm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nom_adm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Cuotas
    cuotas_aportadas: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    cuotas_rescatadas: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    cuotas_en_circulacion: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    valor_cuota: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)

    # Patrimonio y activos
    patrimonio_neto: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    activo_tot: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)

    # Partícipes
    num_participes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_participes_inst: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Comisiones y gastos
    rem_fija: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    rem_variable: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gastos_afectos: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gastos_no_afectos: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    comision_inversion: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    comision_rescate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)

    # Factores
    factor_ajuste: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    factor_reparto: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)

    # Inversión en fondos (monto)
    inversion_en_fondos: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)

    # Flags S/N
    participes_inst: Mapped[str | None] = mapped_column(String(1), nullable=True)
    fondo_pen: Mapped[str | None] = mapped_column(String(1), nullable=True)
