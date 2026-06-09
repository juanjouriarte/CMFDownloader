from __future__ import annotations

from sqlalchemy import update

from src.db.engine import SessionLocal
from src.db.models.fondos_inversion import FondoInversion


def mark_has_data(run_fondo: str, value: bool) -> None:
    with SessionLocal() as session:
        session.execute(
            update(FondoInversion)
            .where(FondoInversion.run_fondo == run_fondo)
            .where(FondoInversion.has_data.is_(None))  # only set once
            .values(has_data=value)
        )
        session.commit()
