from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class Entidad(Base):
    __tablename__ = "entidades"

    rut: Mapped[str] = mapped_column(String(20), primary_key=True)
    nombre_canonical: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo_persona: Mapped[str | None] = mapped_column(String(1), nullable=True)
