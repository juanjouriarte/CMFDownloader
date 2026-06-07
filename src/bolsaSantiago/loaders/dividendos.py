from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.dividendos import Dividendo

logger = logging.getLogger(__name__)


def _to_decimal(val) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return None


def _to_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def load_dividendos(records: list[dict], year: int) -> int:
    rows = [
        {
            "nemo":        r.get("nemo", ""),
            "descrip_vc":  r.get("descrip_vc") or None,
            "fec_lim":     r.get("fec_lim") or None,
            "fec_pago":    r.get("fec_pago") or None,
            "val_acc":     _to_decimal(r.get("val_acc")),
            "moneda":      r.get("moneda") or None,
            "num_acc_ant": _to_int(r.get("num_acc_ant")),
            "num_acc_der": _to_int(r.get("num_acc_der")),
            "num_acc_nue": _to_int(r.get("num_acc_nue")),
            "pre_ant_vc":  _to_decimal(r.get("pre_ant_vc")),
            "pre_ex_vc":   _to_decimal(r.get("pre_ex_vc")),
        }
        for r in records
        if r.get("nemo")
    ]

    if not rows:
        logger.info("Dividendos %d: sin registros", year)
        return 0

    # Deduplicate by unique key — keep last occurrence
    rows = list({(r["nemo"], r["fec_pago"], r["descrip_vc"]): r for r in rows}.values())

    with SessionLocal() as session:
        stmt = insert(Dividendo).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_dividendo",
            set_={c: stmt.excluded[c] for c in rows[0] if c not in ("nemo", "fec_pago", "descrip_vc")},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Dividendos %d: %d registros upserted", year, len(rows))
    return len(rows)
