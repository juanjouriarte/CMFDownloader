from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.engine import SessionLocal
from .deps import CacheHook, Pagination

router = APIRouter(prefix="/investment-funds", tags=["investment-funds"])


class CategoryInfo(BaseModel):
    categoria: str
    tipo: str
    grupo: str
    nombre_cat: str
    confianza: str
    periodo: date


class GeoPct(BaseModel):
    pais: str
    pct_peso: float


class FundFIItem(BaseModel):
    run_fondo: str
    razon_social: str | None
    administrador: str | None
    rescatable: bool | None
    vigente: bool | None
    categoria: str | None
    tipo: str | None
    nombre_cat: str | None


class FIPortfolioPosition(BaseModel):
    source: str
    nemotecnico: str | None
    rut_emisor: str | None
    nombre_emisor: str | None
    tipo_instrumento: str | None
    pct_activo_fondo: float | None
    valorizacion_cierre: float | None
    clasif_riesgo: str | None
    tir_val_par_precio: float | None
    fecha_vencimiento: str | None
    cant_unidades: float | None
    tipo_unidades: str | None
    cod_moneda_liquidacion: str | None
    tipo_interes: str | None
    pct_capital_emisor: float | None
    pct_activo_emisor: float | None
    situacion_instrumento: str | None
    clasif_esf: str | None
    cod_pais: str | None
    nombre_fondo_emisor: str | None


class NavFI(BaseModel):
    serie: str | None
    moneda: str | None
    fecha: date
    valor_cuota: float | None   # valor_libro aliased for consistency with FM
    patrimonio_neto: float | None
    num_aportantes: int | None


class RentFI(BaseModel):
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


class FundFIDetail(FundFIItem):
    series: list[NavFI]
    rentability: list[RentFI]
    category: CategoryInfo | None
    geo_breakdown: list[GeoPct]


class NavPointFI(BaseModel):
    fecha: date
    serie: str | None
    valor_cuota: float | None   # valor_libro aliased for consistency with FM
    patrimonio_neto: float | None


@router.get("", response_model=list[FundFIItem])
def list_investment_funds(
    pagination: Pagination,
    _: CacheHook,
    admin: str | None = Query(None, description="Partial match on administrador name"),
    rescatable: bool | None = Query(None),
    vigente: bool | None = Query(None),
    categoria: str | None = Query(None, description="Category code from categoria_fi"),
    tipo: str | None = Query(None, description="Category type (e.g. 'Accionario', 'Deuda')"),
) -> list[FundFIItem]:
    limit, offset = pagination
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if admin:
        conditions.append("f.administrador ILIKE :admin")
        params["admin"] = f"%{admin}%"
    if rescatable is not None:
        conditions.append("f.rescatable = :rescatable")
        params["rescatable"] = rescatable
    if vigente is not None:
        conditions.append("f.vigente = :vigente")
        params["vigente"] = vigente
    if categoria:
        conditions.append("cat.categoria = :categoria")
        params["categoria"] = categoria
    if tipo:
        conditions.append("cat.tipo = :tipo")
        params["tipo"] = tipo

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = text(f"""
        SELECT f.run_fondo, f.razon_social, f.administrador, f.rescatable, f.vigente,
               cat.categoria, cat.tipo, cat.nombre_cat
        FROM fondos_inversion f
        LEFT JOIN LATERAL (
            SELECT categoria, tipo, nombre_cat
            FROM categoria_fi
            WHERE run_fondo = f.run_fondo
            ORDER BY periodo DESC
            LIMIT 1
        ) cat ON true
        {where}
        ORDER BY f.razon_social
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [FundFIItem(**dict(r)) for r in rows]


@router.get("/{run}", response_model=FundFIDetail)
def get_investment_fund(run: str, _: CacheHook) -> FundFIDetail:
    with SessionLocal() as session:
        fund_row = session.execute(
            text("""
                SELECT f.run_fondo, f.razon_social, f.administrador, f.rescatable, f.vigente,
                       cat.categoria, cat.tipo, cat.nombre_cat
                FROM fondos_inversion f
                LEFT JOIN LATERAL (
                    SELECT categoria, tipo, nombre_cat
                    FROM categoria_fi
                    WHERE run_fondo = f.run_fondo
                    ORDER BY periodo DESC
                    LIMIT 1
                ) cat ON true
                WHERE f.run_fondo = :run
            """),
            {"run": run},
        ).mappings().one_or_none()

        if not fund_row:
            raise HTTPException(404, "Investment fund not found")

        series_rows = session.execute(
            text("""
                SELECT DISTINCT ON (serie) serie, moneda, fecha,
                       valor_libro AS valor_cuota, patrimonio_neto, num_aportantes
                FROM valores_cuota_fi
                WHERE run_fondo = :run
                ORDER BY serie, fecha DESC
            """),
            {"run": run},
        ).mappings().all()

        rent_rows = session.execute(
            text("""
                SELECT r.run_fondo, r.serie, f.razon_social, r.administrador,
                       r.valor_actual, r.fecha_calculo,
                       r.r_1d, r.r_1w, r.r_1m, r.r_1y, r.r_5y, r.r_ytd
                FROM mv_rentabilidad_fi r
                LEFT JOIN fondos_inversion f ON f.run_fondo = r.run_fondo
                WHERE r.run_fondo = :run
            """),
            {"run": run},
        ).mappings().all()

        cat_row = session.execute(
            text("""
                SELECT categoria, tipo, grupo, nombre_cat, confianza, periodo
                FROM categoria_fi
                WHERE run_fondo = :run
                ORDER BY periodo DESC
                LIMIT 1
            """),
            {"run": run},
        ).mappings().one_or_none()

        latest_period = session.execute(
            text("SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run"),
            {"run": run},
        ).scalar_one_or_none()

        geo_rows: list = []
        if latest_period:
            geo_rows = session.execute(
                text("""
                    WITH combined AS (
                        SELECT COALESCE(cod_pais, 'CL') AS pais, pct_activo_fondo AS pct
                        FROM cartera_fi_nac
                        WHERE run_fondo = :run AND periodo = :period AND pct_activo_fondo IS NOT NULL
                        UNION ALL
                        SELECT COALESCE(cod_pais, 'OTHER') AS pais, pct_activo_fondo AS pct
                        FROM cartera_fi_ext
                        WHERE run_fondo = :run AND periodo = :period AND pct_activo_fondo IS NOT NULL
                    )
                    SELECT pais, SUM(pct) AS pct_peso
                    FROM combined
                    GROUP BY pais
                    ORDER BY pct_peso DESC
                """),
                {"run": run, "period": latest_period},
            ).mappings().all()

    return FundFIDetail(
        **dict(fund_row),
        series=[NavFI(**dict(r)) for r in series_rows],
        rentability=[RentFI(**dict(r)) for r in rent_rows],
        category=CategoryInfo(**dict(cat_row)) if cat_row else None,
        geo_breakdown=[GeoPct(**dict(r)) for r in geo_rows],
    )


@router.get("/{run}/nav", response_model=list[NavPointFI])
def get_investment_fund_nav(
    run: str,
    pagination: Pagination,
    _: CacheHook,
    serie: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
) -> list[NavPointFI]:
    limit, offset = pagination
    conditions = ["run_fondo = :run"]
    params: dict = {"run": run, "limit": limit, "offset": offset}

    if serie:
        conditions.append("serie = :serie")
        params["serie"] = serie
    if from_date:
        conditions.append("fecha >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("fecha <= :to_date")
        params["to_date"] = to_date

    where = "WHERE " + " AND ".join(conditions)
    sql = text(f"""
        SELECT fecha, serie, valor_libro AS valor_cuota, patrimonio_neto
        FROM valores_cuota_fi
        {where}
        ORDER BY fecha DESC, serie
        LIMIT :limit OFFSET :offset
    """)

    with SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return [NavPointFI(**dict(r)) for r in rows]


@router.get("/{run}/portfolio", response_model=list[FIPortfolioPosition])
def get_investment_fund_portfolio(
    run: str,
    _: CacheHook,
    period: str | None = Query(None, description="Quarter period YYYY-MM-DD, defaults to latest"),
) -> list[FIPortfolioPosition]:
    """Quarter portfolio positions for an investment fund, enriched with SII company names."""
    with SessionLocal() as session:
        if period:
            latest = period
        else:
            latest = session.execute(
                text("SELECT MAX(periodo) FROM cartera_fi_nac WHERE run_fondo = :run"),
                {"run": run},
            ).scalar_one_or_none()

        if not latest:
            return []

        naci = session.execute(
            text("""
                SELECT 'naci' AS source, c.nemotecnico, c.rut_emisor,
                       COALESCE(e.razon_social, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, c.pct_activo_fondo,
                       c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, c.tipo_interes,
                       c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf, c.cod_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_nac c
                LEFT JOIN emisores e ON e.rut = c.rut_emisor
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemotecnico
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemotecnico
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                WHERE c.run_fondo = :run AND c.periodo = :period
                ORDER BY c.pct_activo_fondo DESC NULLS LAST
            """),
            {"run": run, "period": latest},
        ).mappings().all()

        extr = session.execute(
            text("""
                SELECT 'extr' AS source, c.nemo_isin AS nemotecnico, NULL AS rut_emisor,
                       COALESCE(c.nombre_emisor, fm.nombre_fondo, fi2.razon_social) AS nombre_emisor,
                       c.tipo_instrumento, c.pct_activo_fondo,
                       c.valorizacion_cierre, c.clasif_riesgo,
                       c.tir_val_par_precio, c.fecha_vencimiento, c.cant_unidades,
                       c.tipo_unidades, c.cod_moneda_liquidacion, c.tipo_interes,
                       c.pct_capital_emisor, c.pct_activo_emisor,
                       c.situacion_instrumento, c.clasif_esf, c.cod_pais,
                       COALESCE(fm.nombre_fondo, fi2.razon_social) AS nombre_fondo_emisor
                FROM cartera_fi_ext c
                LEFT JOIN nemotecnicos n ON n.nemotecnico = c.nemo_isin
                LEFT JOIN fondo_mutuo fm ON fm.run_fondo = n.run_fondo
                LEFT JOIN nemotecnicos_fi nfi ON nfi.nemotecnico = c.nemo_isin
                LEFT JOIN fondos_inversion fi2 ON fi2.run_fondo = nfi.run_fondo
                WHERE c.run_fondo = :run AND c.periodo = :period
                ORDER BY c.pct_activo_fondo DESC NULLS LAST
            """),
            {"run": run, "period": latest},
        ).mappings().all()

    return [FIPortfolioPosition(**dict(r)) for r in naci] + [FIPortfolioPosition(**dict(r)) for r in extr]
