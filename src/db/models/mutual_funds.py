from __future__ import annotations

from datetime import date

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class FondoMutuo(Base):
    __tablename__ = "fondo_mutuo"

    run_fondo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nombre_fondo: Mapped[str] = mapped_column(String(255))
    nombre_corto: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rut_administradora: Mapped[str] = mapped_column(String(20))
    razon_social_administradora: Mapped[str] = mapped_column(String(255))
    tipo_fondo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fecha_inicio_operaciones: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_termino_operaciones: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_res_aprobacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    nro_res_aprobacion: Mapped[str | None] = mapped_column(String(50), nullable=True)
