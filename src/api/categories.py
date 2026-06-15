from __future__ import annotations

from datetime import date
from typing import Literal

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


class CategoriaFMItem(BaseModel):
    run_fondo: str
    nombre_fondo: str | None
    administrador: str | None
    categoria: str
    grupo: str
    tipo: str
    nombre_cat: str
    confianza: str
    periodo: date


def _build_catalog(rows) -> list[dict]:
    catalog: list[dict] = []
    by_type: dict[str, dict] = {}
    by_group: dict[tuple[str, str], dict] = {}
    for row in rows:
        type_item = by_type.get(row["tipo"])
        if type_item is None:
            type_item = {"type": row["tipo"], "groups": []}
            by_type[row["tipo"]] = type_item
            catalog.append(type_item)

        group_key = (row["tipo"], row["grupo"])
        group_item = by_group.get(group_key)
        if group_item is None:
            group_item = {"group": row["grupo"], "categories": []}
            by_group[group_key] = group_item
            type_item["groups"].append(group_item)

        group_item["categories"].append({
            "code": row["code"],
            "name": row["name"],
            "fund_count": row["fund_count"],
        })
    return catalog


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


@router.get("/fm", response_model=list[CategoriaFMItem])
def categories_fm(
    pagination: Pagination,
    _: CacheHook,
    categoria: str | None = Query(None),
    tipo: str | None = Query(None),
    admin: str | None = Query(None, description="Partial match on administrador name"),
) -> list[CategoriaFMItem]:
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
        conditions.append("f.razon_social_administradora ILIKE :admin")
        params["admin"] = f"%{admin}%"

    extra = ("AND " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT DISTINCT ON (c.run_fondo)
               c.run_fondo, f.nombre_fondo,
               f.razon_social_administradora AS administrador,
               c.categoria, c.grupo, c.tipo, c.nombre_cat, c.confianza, c.periodo
        FROM categoria_fm c
        LEFT JOIN fondo_mutuo f ON f.run_fondo = c.run_fondo
        WHERE 1=1 {extra}
        ORDER BY c.run_fondo, c.periodo DESC
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [CategoriaFMItem(**dict(r)) for r in rows]


@router.get("/catalog")
def categories_catalog(
    _: CacheHook,
    fund_type: Literal["fm", "fi"] = Query(...),
) -> list[dict]:
    table = "categoria_fm" if fund_type == "fm" else "categoria_fi"
    sql = text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (run_fondo)
                   run_fondo, tipo, grupo, categoria, nombre_cat
            FROM {table}
            ORDER BY run_fondo, periodo DESC
        )
        SELECT tipo, grupo, categoria AS code, nombre_cat AS name,
               COUNT(*) AS fund_count
        FROM latest
        GROUP BY tipo, grupo, categoria, nombre_cat
        ORDER BY tipo, grupo, nombre_cat
    """)
    with SessionLocal() as session:
        rows = session.execute(sql).mappings().all()

    return _build_catalog(rows)
