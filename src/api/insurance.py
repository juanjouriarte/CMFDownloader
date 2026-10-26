"""Public insurance explorer. Reported CLP valuations; no sum of notionals."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from src.db.engine import SessionLocal
from src.etl.insurance.download import PAGE

router = APIRouter(prefix="/industry-workspace/insurance", tags=["insurance"])
ASSETS = {"fixed_income": "Renta fija", "funds": "Fondos", "equities": "Acciones",
          "real_estate": "Inmobiliario", "subsidiaries": "Filiales", "other": "Otros"}
FAMILIES = {"I": "Renta fija", "A": "Acciones y FI", "F": "Fondos mutuos", "B": "Bienes raíces",
            "X": "Inversiones en el extranjero", "O": "Otras inversiones", "P": "Derivados y pactos",
            "C": "Controles", "D": "Descriptores", "T": "Tablas de desarrollo",
            "E": "Flujos elegibles", "G": "Garantías"}
FROM = """FROM insurance_records r JOIN insurance_imports i ON i.id=r.import_id
          JOIN insurance_files f ON f.id=r.file_id"""
VISIBLE = """r.id,r.insurer_rut,f.insurer_name,r.family,r.record_type,r.record_role,
    r.asset_class,r.aggregation_role,r.instrument_type,r.identifier,r.display_name,r.issuer_rut,r.issuer_name,
    r.administrator_rut,r.administrator_name,r.fund_kind,r.fund_run,r.series,r.currency,r.currency_raw,
    r.country,r.rating,r.maturity_date,r.purchase_yield,r.market_yield,r.units,r.nominal,
    r.final_value_clp,r.data_quality,r.quality_flags,
    r.fields->>'long_currency' long_currency,r.fields->>'short_currency' short_currency,
    r.fields->>'asset_value_mclp' asset_value_mclp,r.fields->>'liability_value_mclp' liability_value_mclp,
    r.fields->>'contract_kind' contract_kind"""


def read(sql, params=None):
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        return [dict(row) for row in session.execute(text(sql), params or {}).mappings()]


def _month(period):
    if period and period.day != 1:
        raise HTTPException(422, "El período debe ser el primer día del mes.")


def filters(
    period: date,
    insurer: str | None = Query(None, pattern=r"^\d{1,9}$"),
    administrator: str | None = Query(None, pattern=r"^\d{1,9}$"),
    asset_class: Literal["fixed_income", "funds", "equities", "real_estate", "subsidiaries", "other"] | None = None,
    currency: str | None = Query(None, min_length=1, max_length=16),
    section: Literal["holdings", "contracts", "support"] = "holdings",
    include_complementary: bool = False,
    family: str | None = Query(None, pattern=r"^[AB CDEFGIOPTX]$".replace(" ", "")),
    search: str = Query("", max_length=120),
):
    _month(period)
    where = ["i.active", "i.entity_group='CSVID'", "i.period=:period"]
    params = {"period": period}
    if section == "holdings":
        where.append("r.record_role='holding'" if include_complementary else "r.aggregation_role='primary'")
    else:
        where.append("r.aggregation_role=:section")
        params["section"] = "contract" if section == "contracts" else "auxiliary"
    for field, value in (("insurer_rut", insurer), ("administrator_rut", administrator),
                         ("asset_class", asset_class), ("family", family)):
        if value:
            where.append(f"r.{field}=:{field}")
            params[field] = value
    if currency:
        params["currency"] = currency
        if section == "contracts":
            # Swaps report each leg separately; a NULL headline currency must
            # not hide them when filtering the denomination of either leg.
            aliases = {"CLP": ["$$", "$", "CLP"], "UF": ["UF", "CLF"],
                       "USD": ["PROM", "US$"], "USD_SEGURO": ["USD"], "AUD": ["DA", "AUD"]}
            params["raw_currencies"] = aliases.get(currency, [currency])
            where.append("""(r.currency=:currency OR r.fields->>'long_currency'=ANY(:raw_currencies)
                OR r.fields->>'short_currency'=ANY(:raw_currencies))""")
        else:
            where.append("r.currency=:currency")
    if search.strip():
        where.append("concat_ws(' ',r.display_name,r.identifier,r.issuer_name,r.insurer_rut,r.fund_run,r.instrument_type,r.administrator_name) ILIKE :search")
        params["search"] = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return " AND ".join(where), params


@router.get("/directory")
def directory(response: Response, period: date | None = None):
    _month(period)
    response.headers["Cache-Control"] = "no-cache"
    periods = read("""SELECT id,period,loaded_at,checked_at,accepted_records,
        (SELECT count(*) FROM insurance_files f WHERE f.import_id=i.id AND f.status='quarantined') quarantined_files
        FROM insurance_imports i WHERE active AND entity_group='CSVID' ORDER BY period DESC""")
    selected = period or (periods[0]["period"] if periods else None)
    base = dict(periods=periods, selected_period=selected, asset_classes=ASSETS, families=FAMILIES,
                source_url=PAGE + "?tipoentidad=CSVID", schedule="08:55 America/Santiago",
                group="CSVID", group_label="Compañías de seguros de vida")
    if not selected:
        return base | {"insurers": [], "administrators": [], "currencies": []}
    params = {"period": selected}
    insurers = read("""SELECT f.insurer_rut rut, max(f.insurer_name) name,
        count(*) FILTER(WHERE f.status='quarantined') excluded_files
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period GROUP BY f.insurer_rut ORDER BY name""", params)
    admins = read("""SELECT r.administrator_rut rut,max(r.administrator_name) name,
        count(DISTINCT r.insurer_rut) insurers """ + FROM + """
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period AND r.administrator_rut IS NOT NULL
          AND r.asset_class='funds' AND r.aggregation_role='primary'
        GROUP BY r.administrator_rut ORDER BY name NULLS LAST,r.administrator_rut""", params)
    currencies = read("""SELECT DISTINCT r.currency """ + FROM + """
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period AND r.currency IS NOT NULL ORDER BY r.currency""", params)
    return base | {"insurers": insurers, "administrators": admins, "currencies": [r["currency"] for r in currencies]}


@router.get("/summary")
def summary(response: Response, selected=Depends(filters), include_breakdowns: bool = False):
    response.headers["Cache-Control"] = "no-cache"
    where, params = selected
    metrics = read("""SELECT count(*) records,count(DISTINCT r.insurer_rut) insurers,
        count(DISTINCT (r.fund_kind,r.fund_run)) FILTER(WHERE r.fund_run IS NOT NULL) funds,
        sum(r.final_value_clp) FILTER(WHERE r.aggregation_role='primary') value_clp,
        count(*) FILTER(WHERE r.aggregation_role='primary' AND r.final_value_clp IS NULL) unvalued_records,
        count(*) FILTER(WHERE r.aggregation_role='complementary') complementary_records,
        count(*) FILTER(WHERE r.quality_flags<>'[]'::jsonb OR r.data_quality<>'validated') warning_records
        """ + FROM + " WHERE " + where, params)[0]
    def distribution(column):
        return read(f"""SELECT {column} key, count(*) records, sum(r.final_value_clp) value_clp
            {FROM} WHERE {where} AND r.aggregation_role='primary'
            GROUP BY {column} ORDER BY value_clp DESC NULLS LAST,key""", params)
    history = read("""SELECT i.period,count(*) records,count(DISTINCT r.insurer_rut) insurers,
        sum(r.final_value_clp) FILTER(WHERE r.aggregation_role='primary') value_clp """ + FROM +
        " WHERE " + where.replace("i.period=:period", "true") +
        " GROUP BY i.period ORDER BY i.period", params)
    currency_history, asset_currencies = [], []
    if include_breakdowns:
        currency_history = read(f"""SELECT i.period,r.currency key,count(*) records,
            sum(r.final_value_clp) value_clp {FROM}
            WHERE {where.replace("i.period=:period", "true")} AND r.aggregation_role='primary'
            GROUP BY i.period,r.currency ORDER BY i.period,r.currency NULLS LAST""", params)
        asset_currencies = read(f"""SELECT r.asset_class,r.currency,count(*) records,
            sum(r.final_value_clp) value_clp {FROM}
            WHERE {where} AND r.aggregation_role='primary'
            GROUP BY r.asset_class,r.currency ORDER BY r.asset_class,r.currency NULLS LAST""", params)
    return dict(metrics=metrics, assets=distribution("r.asset_class"), currencies=distribution("r.currency"),
                currency_history=currency_history, asset_currencies=asset_currencies,
                history=history, amount_unit="CLP",
                valuation="Valor reportado por CMF. No es el AUM total de la industria aseguradora.",
                comparison="Muestra observada por mes; las diferencias de valoración no son flujos.")


def fund_holdings(where, params):
    return read(f"""WITH fm AS (
        SELECT DISTINCT ON(run_fondo) run_fondo,tipo,grupo,nombre_cat,periodo
        FROM categoria_fm_effective WHERE periodo<=CURRENT_DATE
        ORDER BY run_fondo,periodo DESC
      ), fi AS (
        SELECT DISTINCT ON(run_fondo) run_fondo,tipo,grupo,nombre_cat,periodo
        FROM categoria_fi_effective WHERE periodo<=CURRENT_DATE
        ORDER BY run_fondo,periodo DESC
      ), classifications AS (
        SELECT 'fm' kind,* FROM fm UNION ALL SELECT 'fi' kind,* FROM fi
      ), positions AS (
        SELECT r.*,i.period,f.insurer_name,c.tipo,c.grupo,c.nombre_cat,c.periodo classification_period,
          CASE WHEN r.fund_kind IN ('fm','fi') AND r.fund_run IS NOT NULL
               THEN r.fund_kind||':'||r.fund_run
               WHEN r.fund_kind='fip' AND NULLIF(r.fields->>'private_fund_rut','0') IS NOT NULL
               THEN 'fip:'||(r.fields->>'private_fund_rut')
               ELSE COALESCE(r.fund_kind,'unknown')||':'||
                 COALESCE(NULLIF(r.identifier,''),'record:'||r.id::text) END fund_key
        {FROM} LEFT JOIN classifications c ON c.kind=r.fund_kind AND c.run_fondo=r.fund_run
        WHERE {where}
      )
      SELECT period,insurer_rut,max(insurer_name) insurer_name,fund_key,fund_kind,fund_run,
        max(display_name) name,administrator_rut,max(administrator_name) administrator_name,
        currency,tipo,grupo,nombre_cat category,max(classification_period) classification_period,
        array_agg(DISTINCT series ORDER BY series) FILTER(WHERE series IS NOT NULL) series,
        array_agg(DISTINCT family) families,
        jsonb_agg(jsonb_build_object('series',series,'units',units)) share_positions,
        count(*) positions,sum(final_value_clp) value_clp,
        count(*) FILTER(WHERE final_value_clp IS NULL) unvalued_positions,
        count(*) FILTER(WHERE data_quality<>'validated' OR quality_flags<>'[]'::jsonb) warning_positions
      FROM positions
      GROUP BY period,insurer_rut,fund_key,fund_kind,fund_run,administrator_rut,currency,tipo,grupo,nombre_cat
      ORDER BY value_clp DESC NULLS LAST,insurer_rut,fund_key,administrator_rut,currency""", params)


@router.get("/fund-heatmap")
def fund_heatmap(
    response: Response, period: date,
    insurer: str | None = Query(None, pattern=r"^\d{1,9}$"),
    administrator: str | None = Query(None, pattern=r"^\d{1,9}$"),
    currency: str | None = Query(None, min_length=1, max_length=16),
    search: str = Query("", max_length=120),
):
    """Fund holdings only, classified by exact (kind, RUN), never by name.

    Use the current effective classification, including manual overrides. Do not
    pretend it was the classification reported by the insurer in that month.
    Series/subportfolios sum into a fund; unclassified positions remain visible.
    """
    response.headers["Cache-Control"] = "no-cache"
    where, params = filters(
        period=period, insurer=insurer, administrator=administrator,
        currency=currency, search=search, asset_class="funds",
        section="holdings", include_complementary=False, family=None,
    )
    holdings = fund_holdings(where, params)
    # Include insurers with no accepted fund rows. A blank cell means no observed
    # holding in the filtered sample, not proof of no investment.
    insurers = read("""SELECT f.insurer_rut rut,max(f.insurer_name) name,
        count(*) FILTER(WHERE f.family IN ('A','F','X') AND f.status<>'validated') problem_files
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period
          AND (:insurer IS NULL OR f.insurer_rut=:insurer)
        GROUP BY f.insurer_rut ORDER BY name""", dict(period=period, insurer=insurer))
    return dict(period=period, funds=holdings, insurers=insurers, amount_unit="CLP",
                classification_basis="current_effective",
                classification_note="Clasificación actual del fondo, incluidas correcciones manuales; no una clasificación histórica de la aseguradora.",
                scope="Fondos reportados en posiciones principales aceptadas. Sin clasificación se conserva dentro del total.")


@router.get("/fund-history")
def fund_history(
    response: Response, period: date, months: int = Query(6, ge=1, le=60),
    from_month: date | None = None,
    insurer: str | None = Query(None, pattern=r"^\d{1,9}$"),
    administrator: str | None = Query(None, pattern=r"^\d{1,9}$"),
    currency: str | None = Query(None, min_length=1, max_length=16),
    search: str = Query("", max_length=120),
    tipo: str | None = Query(None, max_length=160),
    grupo: str | None = Query(None, max_length=160),
    category: str | None = Query(None, max_length=250),
    include_fund_metrics: bool = True,
    include_investors: bool = False,
):
    from .insurance_history import shift, load_bases, build_history
    _month(period)
    _month(from_month)
    start = from_month or shift(period, 1-months)
    if not shift(period, -59) <= start <= period:
        raise HTTPException(422, "El rango debe ser de uno a 60 meses.")
    response.headers["Cache-Control"] = "no-cache"
    where, params = filters(period=period, insurer=insurer, administrator=administrator,
        currency=currency, search=search, asset_class="funds", section="holdings",
        include_complementary=False, family=None)
    params.update(start=shift(start, -1), end=period)
    holdings = fund_holdings(where.replace("i.period=:period", "i.period BETWEEN :start AND :end"), params)
    holdings = [f for f in holdings if all(
        selected is None or (f[field] or "Sin clasificación") == selected
        for field, selected in (("tipo", tipo), ("grupo", grupo), ("category", category)))]
    reports = read("""SELECT i.period,f.insurer_rut,f.family,f.status
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period BETWEEN :start AND :end
          AND f.family IN ('A','F','X')""", params)
    available = [r["period"] for r in read(
        "SELECT period FROM insurance_imports WHERE active AND entity_group='CSVID' ORDER BY period")]
    nav_periods = [p for p in available if params["start"] <= p <= period]
    bases, owners = load_bases(read, holdings, nav_periods) if include_fund_metrics else ({}, {})
    result = build_history(holdings, reports, available, start, period, bases, owners,
                           allow_reported=not administrator and not currency)
    if include_investors:
        from .insurance_investors import add_investors
        add_investors(result, holdings, reports, available, start, period)
    return result


@router.get("/records")
def records(response: Response, selected=Depends(filters),
            sort: Literal["value", "name", "insurer", "maturity", "yield"] = "value",
            direction: Literal["asc", "desc"] = "desc",
            limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    response.headers["Cache-Control"] = "no-cache"
    where, params = selected
    params |= dict(limit=limit, offset=offset)
    column = {"value": "r.final_value_clp", "name": "r.display_name", "insurer": "f.insurer_name",
              "maturity": "r.maturity_date", "yield": "r.purchase_yield"}[sort]
    total = read("SELECT count(*) total " + FROM + " WHERE " + where, params)[0]["total"]
    # Materialize only IDs before reading the wide JSON fields. Otherwise a
    # large sort can detoast every holding just to return the first 50 rows.
    rows = read(f"""WITH page AS MATERIALIZED (
        SELECT r.id {FROM} WHERE {where}
        ORDER BY {column} {direction} NULLS LAST,r.id LIMIT :limit OFFSET :offset
        )
        SELECT {VISIBLE} {FROM} JOIN page ON page.id=r.id
        ORDER BY {column} {direction} NULLS LAST,r.id""", params)
    return dict(total=total, rows=rows, limit=limit, offset=offset)


def portfolio_filters(
    period: date,
    insurer: str | None = Query(None, pattern=r"^\d{1,9}$"),
    administrator: str | None = Query(None, pattern=r"^\d{1,9}$"),
    asset_class: Literal["fixed_income", "funds", "equities", "real_estate", "subsidiaries", "other"] | None = None,
    currency: str | None = Query(None, min_length=1, max_length=16),
    instrument_type: str | None = Query(None, min_length=1, max_length=20),
    search: str = Query("", max_length=120),
):
    where, params = filters(period, insurer, administrator, asset_class, currency,
                           "holdings", False, None, search)
    if instrument_type:
        where += " AND r.instrument_type=:instrument_type"
        params["instrument_type"] = instrument_type
    return where, params


@router.get("/portfolio-heatmap")
def portfolio_heatmap(response: Response, selected=Depends(portfolio_filters),
                      limit: int = Query(24, ge=1, le=60), offset: int = Query(0, ge=0),
                      group_by: Literal["instrument", "issuer"] = "instrument",
                      issuer: str | None = Query(None, pattern=r"^[1-9]\d{0,8}$")):
    from .insurance_portfolio import matrix, FAMILIES
    response.headers["Cache-Control"] = "no-cache"
    where, params = selected
    asset = params.get("asset_class")
    result = matrix(read, where, params, ASSETS, asset, limit, offset, group_by, issuer)
    families = FAMILIES.get(asset, sorted({f for fs in FAMILIES.values() for f in fs}))
    result["insurers"] = read("""SELECT f.insurer_rut rut,max(f.insurer_name) name,
        count(*) FILTER(WHERE f.family=ANY(:families) AND f.status<>'validated') problem_files
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period
          AND (:insurer IS NULL OR f.insurer_rut=:insurer)
        GROUP BY f.insurer_rut ORDER BY name""",
        dict(period=params["period"], insurer=params.get("insurer_rut"), families=families))
    result["types"] = read("""SELECT r.instrument_type key,count(*) records """ + FROM +
        " WHERE " + where.replace(" AND r.instrument_type=:instrument_type", "") +
        " GROUP BY r.instrument_type ORDER BY records DESC,key", params) if asset else []
    return result


@router.get("/portfolio-history")
def portfolio_history(response: Response, selected=Depends(portfolio_filters),
                      months: int = Query(6, ge=1, le=60), from_month: date | None = None,
                      limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                      sort: str = Query("current", pattern=r"^(current|name|\d{4}-\d{2}-01)$"),
                      direction: Literal["asc", "desc"] = "desc",
                      mode: Literal["value", "delta", "deltaPct"] = "value",
                      issuer: str | None = Query(None, pattern=r"^[1-9]\d{0,8}$")):
    from .insurance_portfolio import history
    from .insurance_history import shift
    where, params = selected
    if issuer:
        where += " AND r.issuer_rut=:issuer"
        params["issuer"] = issuer
    if not params.get("insurer_rut"):
        raise HTTPException(422, "Selecciona una aseguradora para consultar su historial.")
    end = params["period"]
    _month(from_month)
    start = from_month or shift(end, 1-months)
    if not shift(end, -59) <= start <= end:
        raise HTTPException(422, "El rango debe ser de uno a 60 meses.")
    if sort not in {"current", "name"}:
        try:
            valid_sort = start <= date.fromisoformat(sort) <= end
        except ValueError:
            valid_sort = False
        if not valid_sort:
            raise HTTPException(422, "El mes de orden debe estar dentro del rango.")
    response.headers["Cache-Control"] = "no-cache"
    params.update(start=shift(start, -1), end=end)
    return history(read, where.replace("i.period=:period", "i.period BETWEEN :start AND :end"),
                   params, ASSETS, params.get("asset_class"), params["insurer_rut"],
                   start, end, limit, offset, sort, direction, mode)


@router.get("/records/{record_id}")
def detail(record_id: int, response: Response):
    response.headers["Cache-Control"] = "no-cache"
    rows = read(f"""SELECT {VISIBLE},r.fields,r.source_line,r.source_sha256,f.filename,f.status file_status,
        f.reason file_reason,f.sha256 file_sha256,i.period,i.archive_sha256,i.loaded_at,a.source_url
        {FROM} JOIN insurance_archives a ON a.sha256=i.archive_sha256
        WHERE i.active AND i.entity_group='CSVID' AND r.id=:id""", {"id": record_id})
    if not rows:
        raise HTTPException(404, "Posición no disponible. Actualiza la cartera.")
    row = rows[0]
    # Personal mortgage identifiers are irrelevant to the public market explorer.
    row["fields"].pop("mortgage_debtor_rut", None)
    row["fields"].pop("mortgage_debtor_dv", None)
    return row


@router.get("/coverage")
def coverage(response: Response, period: date, insurer: str | None = Query(None, pattern=r"^\d{1,9}$")):
    _month(period)
    response.headers["Cache-Control"] = "no-cache"
    condition = " AND f.insurer_rut=:insurer" if insurer else ""
    rows = read("""SELECT f.filename,f.family,f.insurer_rut,f.insurer_name,f.status,f.accepted_rows,
        f.source_rows,f.reason,f.declared_records,f.actual_records
        FROM insurance_files f JOIN insurance_imports i ON i.id=f.import_id
        WHERE i.active AND i.entity_group='CSVID' AND i.period=:period""" + condition +
        " ORDER BY f.insurer_rut,f.family", dict(period=period, insurer=insurer))
    return dict(files=rows, scope="Archivos del mes y aseguradora; no depende de los filtros de instrumento o AGF.",
                summary={status: sum(r["status"] == status for r in rows) for status in
                         ("validated", "accepted_with_warning", "partially_reconciled", "quarantined")})
