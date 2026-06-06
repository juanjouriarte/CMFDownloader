from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class BonoNemotecnico(Base):
    __tablename__ = "bonos_nemotecnicos"

    nemotecnico: Mapped[str] = mapped_column(String(20), primary_key=True)
    fecha_ingreso: Mapped[date | None] = mapped_column(Date, nullable=True)
    nombre_emisor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rut_emisor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    numero_inscripcion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fecha_inscripcion: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_colocacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    tasa_fiscal: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
