from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.entidades import Entidad

logger = logging.getLogger(__name__)


def refresh_entidades() -> int:
    """
    Rebuild entidades from aportantes_fi:
    - canonical name = most frequent nombre per RUT
    - then backfill aportantes_fi.nombre_canonical via JOIN

    Safe to re-run at any time.
    """
    with SessionLocal() as session:
        # Build canonical name per RUT (most frequent nombre wins)
        rows = session.execute(text("""
            SELECT DISTINCT ON (rut)
                rut,
                tipo_persona,
                nombre AS nombre_canonical
            FROM (
                SELECT rut, tipo_persona, nombre, COUNT(*) AS cnt
                FROM aportantes_fi
                WHERE rut IS NOT NULL AND nombre IS NOT NULL
                GROUP BY rut, tipo_persona, nombre
            ) sub
            ORDER BY rut, cnt DESC, nombre
        """)).fetchall()

        if not rows:
            logger.warning("No aportantes data found")
            return 0

        records = [
            {"rut": r.rut, "nombre_canonical": r.nombre_canonical, "tipo_persona": r.tipo_persona}
            for r in rows
        ]

        stmt = insert(Entidad).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["rut"],
            set_={"nombre_canonical": stmt.excluded.nombre_canonical,
                  "tipo_persona": stmt.excluded.tipo_persona},
        )
        session.execute(stmt)

        # Backfill nombre_canonical on aportantes_fi
        session.execute(text("""
            UPDATE aportantes_fi a
            SET nombre_canonical = e.nombre_canonical
            FROM entidades e
            WHERE a.rut = e.rut
              AND (a.nombre_canonical IS NULL OR a.nombre_canonical != e.nombre_canonical)
        """))

        session.commit()

    logger.info("Entidades: %d canonical names upserted", len(records))
    return len(records)
