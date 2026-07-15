from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class FondoInversion(Base):
    __tablename__ = "fondos_inversion"
    __table_args__ = (
        Index("ix_fondos_inversion_administrador", "administrador"),
        Index("ix_fondos_inversion_vigente_rescatable", "vigente", "rescatable"),
    )

    run_fondo: Mapped[str] = mapped_column(String(20), primary_key=True)
    dv_fondo: Mapped[str | None] = mapped_column(String(5), nullable=True)
    razon_social: Mapped[str | None] = mapped_column(Text, nullable=True)
    administrador: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescatable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vigente: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_data: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)


class NemotecnicoFI(Base):
    __tablename__ = "nemotecnicos_fi"

    nemotecnico: Mapped[str] = mapped_column(String(20), primary_key=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    dv_fondo: Mapped[str | None] = mapped_column(String(5), nullable=True)
    razon_social: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nombre_fondo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    serie: Mapped[str | None] = mapped_column(String(50), nullable=True)
    admin_rut: Mapped[str | None] = mapped_column(String(20), nullable=True)
    admin_dv: Mapped[str | None] = mapped_column(String(5), nullable=True)
    admin_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
