from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/rentability", tags=["rentability"])

_VALID_SORT = {"r_1d", "r_1w", "r_1m", "r_1y", "r_5y", "r_ytd"}


class RentFMItem(BaseModel):
    run_fondo: str
    serie: str | None
    nombre_fondo: str | None
    administrador: str | None
    valor_actual: float | None
    fecha_calculo: date | None
    r_1d: float | None
    r_1w: float | None
    r_1m: float | None
    r_1y: float | None
    r_5y: float | None
    r_ytd: float | None
    is_data_suspicious: bool
    suspicious_periods: list[str]


class RentFIItem(BaseModel):
    run_fondo: str
    serie: str | None
    razon_social: str | None
    administrador: str | None
    valor_actual: float | None
    fecha_calculo: date | None
    r_1d: float | None
    r_1w: float | None
    r_1m: float | None
    r_1y: float | None
    r_5y: float | None
    r_ytd: float | None


@router.get("/fm", response_model=list[RentFMItem])
def rentability_fm(
    pagination: Pagination,
    _: CacheHook,
    sort: str = Query("r_1y", description="Sort field: r_1d | r_1w | r_1m | r_1y | r_5y | r_ytd"),
    admin: str | None = Query(None, description="Partial match on administrador name"),
    categoria: str | None = Query(None, description="Category code from categoria_fm"),
    tipo: str | None = Query(None, description="Category type (e.g. 'Accionario', 'Deuda')"),
    include_suspicious: bool = Query(
        False,
        description="Include rows flagged for implausible return values",
    ),
) -> list[RentFMItem]:
    if sort not in _VALID_SORT:
        sort = "r_1y"
    limit, offset = pagination
    conditions: list[str] = [f"r.{sort} IS NOT NULL"]
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("r.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if categoria:
        conditions.append("c.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("c.tipo = :tipo")
        params["tipo"] = tipo
    if not include_suspicious:
        conditions.append("NOT r.is_data_suspicious")

    where = "WHERE " + " AND ".join(conditions)
    sql = text(f"""
        WITH latest_cat AS (
            SELECT DISTINCT ON (run_fondo) run_fondo, categoria, tipo
            FROM categoria_fm ORDER BY run_fondo, periodo DESC
        )
        SELECT r.run_fondo, r.serie, r.nombre_fondo, r.administrador, r.valor_actual,
               r.fecha_calculo, r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd,
               r.is_data_suspicious, r.suspicious_periods
        FROM v_rentabilidad_fm_quality r
        LEFT JOIN latest_cat c ON c.run_fondo = r.run_fondo
        {where}
        ORDER BY r.{sort} DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [RentFMItem(**dict(r)) for r in rows]


@router.get("/fi", response_model=list[RentFIItem])
def rentability_fi(
    pagination: Pagination,
    _: CacheHook,
    sort: str = Query("r_1y", description="Sort field: r_1d | r_1w | r_1m | r_1y | r_5y | r_ytd"),
    admin: str | None = Query(None, description="Partial match on administrador name"),
    categoria: str | None = Query(None, description="Category code from categoria_fi"),
    tipo: str | None = Query(None, description="Category type (e.g. 'Accionario', 'Deuda')"),
) -> list[RentFIItem]:
    if sort not in _VALID_SORT:
        sort = "r_1y"
    limit, offset = pagination
    conditions: list[str] = [f"r.{sort} IS NOT NULL"]
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("r.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if categoria:
        conditions.append("c.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("c.tipo = :tipo")
        params["tipo"] = tipo

    where = "WHERE " + " AND ".join(conditions)
    sql = text(f"""
        WITH latest_cat AS (
            SELECT DISTINCT ON (run_fondo) run_fondo, categoria, tipo
            FROM categoria_fi ORDER BY run_fondo, periodo DESC
        )
        SELECT r.run_fondo, r.serie, f.razon_social, r.administrador, r.valor_actual,
               r.fecha_calculo, r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
        FROM mv_rentabilidad_fi r
        LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
        LEFT JOIN latest_cat c ON c.run_fondo = r.run_fondo
        {where}
        ORDER BY r.{sort} DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [RentFIItem(**dict(r)) for r in rows]
