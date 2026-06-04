from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class Nemotecnico(Base):
    __tablename__ = "nemotecnicos"

    nemotecnico: Mapped[str] = mapped_column(String(20), primary_key=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    dv_fondo: Mapped[str | None] = mapped_column(String(5), nullable=True)
    razon_social: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nombre_fondo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    serie: Mapped[str | None] = mapped_column(String(50), nullable=True)
    admin_rut: Mapped[str | None] = mapped_column(String(20), nullable=True)
    admin_dv: Mapped[str | None] = mapped_column(String(5), nullable=True)
    admin_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
