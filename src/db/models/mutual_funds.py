from __future__ import annotations

from datetime import date

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class FondoMutuo(Base):
    """Registro de identificación de Fondos Mutuos — fuente: CMF fm_identidad.txt"""

    __tablename__ = "fondo_mutuo"

    # CMF uses RUN as the stable fund identifier
    run_fondo: Mapped[str] = mapped_column(String(20), primary_key=True)
    nombre_fondo: Mapped[str] = mapped_column(String(255))
    run_sociedad: Mapped[str] = mapped_column(String(20))
    nombre_sociedad: Mapped[str] = mapped_column(String(255))
    tipo_fondo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fecha_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
