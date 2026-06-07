from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/categories", tags=["categories"])


class CategoriaFIItem(BaseModel):
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    categoria: str
    grupo: str
    tipo: str
    nombre_cat: str
    confianza: str
    periodo: date


@router.get("/fi", response_model=list[CategoriaFIItem])
def categories_fi(
    pagination: Pagination,
    _: CacheHook,
    categoria: str | None = Query(None),
    tipo: str | None = Query(None),
    admin: str | None = Query(None, description="Partial match on administrador name"),
) -> list[CategoriaFIItem]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if categoria:
        conditions.append("c.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("c.tipo = :tipo")
        params["tipo"] = tipo
    if admin:
        conditions.append("f.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"

    extra = ("AND " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT DISTINCT ON (c.run_fondo)
               c.run_fondo, f.razon_social, f.administrador,
               c.categoria, c.grupo, c.tipo, c.nombre_cat, c.confianza, c.periodo
        FROM categoria_fi c
        LEFT JOIN fondos_inversion f ON f.run_fondo = c.run_fondo
        WHERE 1=1 {extra}
        ORDER BY c.run_fondo, c.periodo DESC
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [CategoriaFIItem(**dict(r)) for r in rows]
