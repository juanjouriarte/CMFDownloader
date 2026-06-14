from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook

router = APIRouter(prefix="/countries", tags=["countries"])


class CountryItem(BaseModel):
    code: str
    name: str
    updated_at: datetime


@router.get("", response_model=list[CountryItem])
def list_countries(_: CacheHook) -> list[CountryItem]:
    """All CMF country codes and their names, sorted alphabetically by name."""
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT code, name, updated_at FROM countries ORDER BY name")
        ).mappings().all()
    return [CountryItem(**dict(r)) for r in rows]
