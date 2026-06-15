from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook

router = APIRouter(tags=["reference"])


class RefCodeItem(BaseModel):
    domain: str
    code: str
    name: str
    updated_at: datetime


@router.get("/ref-codes", response_model=list[RefCodeItem])
def list_ref_codes(
    _: CacheHook,
    domain: str | None = Query(None, description="Filter by domain: country, currency, instrument"),
) -> list[RefCodeItem]:
    """CMF reference codes — countries, currencies, and instrument types."""
    params: dict = {}
    where = ""
    if domain:
        where = "WHERE domain = :domain"
        params["domain"] = domain

    sql = text(f"SELECT domain, code, name, updated_at FROM ref_codes {where} ORDER BY domain, name")
    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [RefCodeItem(**dict(r)) for r in rows]
