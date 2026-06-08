from __future__ import annotations

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class SIIEmisor(Base):
    __tablename__ = "emisores"
    __table_args__ = (
        Index("ix_emisores_razon_social", "razon_social"),
    )

    rut: Mapped[str] = mapped_column(String(20), primary_key=True)
    dv: Mapped[str | None] = mapped_column(String(2), nullable=True)
    razon_social: Mapped[str] = mapped_column(Text, nullable=False)
