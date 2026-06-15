from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/admins", tags=["admins"])


class AdminItem(BaseModel):
    rut: str | None
    nombre: str | None
    funds_fm: int
    funds_fm_vigente: int
    funds_fi: int
    funds_fi_vigente: int
    funds_total: int


@router.get("", response_model=list[AdminItem])
def list_admins(
    pagination: Pagination,
    _: CacheHook,
    search: str | None = Query(None, description="Partial match on nombre"),
) -> list[AdminItem]:
    """List all administradoras with their fund counts across FM and FI."""
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if search:
        conditions.append("nombre ILIKE :search")
        params["search"] = f"%{search}%"

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT rut, nombre, funds_fm, funds_fm_vigente, funds_fi, funds_fi_vigente, funds_total
        FROM mv_administradores
        {where}
        ORDER BY funds_total DESC
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [AdminItem(**dict(r)) for r in rows]


@router.get("/{rut}", response_model=AdminItem)
def get_admin(rut: str, _: CacheHook) -> AdminItem:
    """Get a single administradora by RUT."""
    sql = text("""
        SELECT rut, nombre, funds_fm, funds_fm_vigente, funds_fi, funds_fi_vigente, funds_total
        FROM mv_administradores
        WHERE rut = :rut
    """)
    with SessionLocal() as session:
        row = session.execute(sql, {"rut": rut}).mappings().one_or_none()

    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "Administradora not found")
    return AdminItem(**dict(row))
