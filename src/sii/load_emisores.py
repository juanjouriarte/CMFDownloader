from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.emisores import SIIEmisor

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000


def load_sii_emisores(path: Path) -> int:
    """Load SII company registry into emisores table."""
    logger.info("Reading %s", path)
    df = pd.read_csv(
        path,
        sep="\t",
        dtype=str,
        usecols=["RUT", "DV", "Razón social"],
        encoding="utf-8",
    )
    df.columns = ["rut", "dv", "razon_social"]
    df = df.dropna(subset=["rut", "razon_social"])
    df["rut"] = df["rut"].str.strip()
    df["dv"] = df["dv"].str.strip()
    df["razon_social"] = df["razon_social"].str.strip()
    df = df.drop_duplicates(subset=["rut"])

    records = df.to_dict("records")
    logger.info("Upserting %d emisores", len(records))

    total = 0
    with SessionLocal() as session:
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            stmt = insert(SIIEmisor).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["rut"],
                set_={
                    "dv": stmt.excluded.dv,
                    "razon_social": stmt.excluded.razon_social,
                },
            )
            session.execute(stmt)
            session.commit()
            total += len(batch)
            logger.info("Upserted %d / %d", total, len(records))

    return total


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("downloads/sii_emisores/sii_dbb.txt")
    print(load_sii_emisores(path), "rows upserted")
