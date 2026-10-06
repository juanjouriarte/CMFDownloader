"""Currency-separated series returns and fund net-new-money rankings."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from math import isfinite
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from src.api.classification_admin import classification_cache_epoch
from src.api.industry import _currency, FM_CURRENCY
from .administrator_timeseries import flow_period_data, flow_windows, validate_range
from .investment_flows import flow_period_data as fi_flow_period_data

router = APIRouter(prefix='/rankings')
_flow_pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='ranking-flows')
PERIOD_COLUMNS = {'1D': 'r_1d', '1W': 'r_1w', '1M': 'r_1m',
                  '1A': 'r_1y', '5A': 'r_5y', 'YTD': 'r_ytd'}


def finite(value):
    return float(value) if value is not None and isfinite(float(value)) else None


def flow_data(kind,currency,start,end):
    """Bound large market scans by fund, never by date: each fund keeps its
    complete baseline/history and all source currencies for FI validation.
    A shared two-worker pool caps database concurrency across requests.
    """
    from .router import rows
    query=fi_flow_period_data if kind=='fi' else flow_period_data
    periods=[('ranking',start)]
    if (end-start).days<=400:
        return query(currency,None,end,None,periods)
    source,registry,alias,active,expression=(
        ('cartola_diaria','fondo_mutuo','cd','f.fecha_termino_operaciones IS NULL',FM_CURRENCY)
        if kind=='fm' else ('valores_cuota_fi','fondos_inversion','v','f.vigente IS TRUE',_currency('v.moneda')))
    candidates=rows(f"""SELECT DISTINCT {alias}.run_fondo run FROM {source} {alias}
        JOIN {registry} f USING(run_fondo) WHERE {alias}.fecha BETWEEN :start AND :end
        AND {expression}=:currency AND {active} ORDER BY {alias}.run_fondo""",
        dict(start=start,end=end,currency=currency))
    runs=[r['run'] for r in candidates]
    futures=[_flow_pool.submit(query,currency,None,end,None,periods,runs[i:i+80])
             for i in range(0,len(runs),80)]
    try:
        return [row for future in futures for row in future.result()]
    except Exception:
        for future in futures:future.cancel()
        raise


@lru_cache(maxsize=24)
def load_returns(kind, currency, bucket):
    from .router import rows
    view, source, registry, name, admin, active = (
        ('v_rentabilidad_fm_quality', 'cartola_diaria', 'fondo_mutuo',
         'nombre_fondo', 'razon_social_administradora', 'f.fecha_termino_operaciones IS NULL')
        if kind == 'fm' else
        ('mv_rentabilidad_fi', 'valores_cuota_fi', 'fondos_inversion',
         'razon_social', 'administrador', 'f.vigente IS TRUE AND f.rescatable IS TRUE'))
    data = rows(f"""SELECT r.run_fondo run, r.serie, r.fecha_calculo date,
        f.{name} name, COALESCE(f.{admin},'Sin administradora') admin,
        c.nombre_cat category, c.tipo category_type, c.grupo category_group,
        {','.join('r.'+column for column in PERIOD_COLUMNS.values())},
        {'r.is_data_suspicious' if kind == 'fm' else 'false'} suspicious
        FROM {view} r JOIN {registry} f USING(run_fondo)
        JOIN {source} n ON n.run_fondo=r.run_fondo AND n.fecha=r.fecha_calculo
          AND n.serie IS NOT DISTINCT FROM r.serie
        LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat,tipo,grupo
          FROM categoria_{kind}_effective WHERE periodo<=CURRENT_DATE
          ORDER BY run_fondo,periodo DESC) c ON c.run_fondo=r.run_fondo
        WHERE {active} AND {_currency('n.moneda')}=:currency
          AND r.fecha_calculo<=CURRENT_DATE AND n.patrimonio_neto>=0
          AND n.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')
        ORDER BY r.run_fondo,r.serie""", dict(currency=currency))
    valid = [r for r in data if not r['suspicious']]
    return dict(kind=kind, currency=currency, methodology='cmf_backend_return',
        date=max((r['date'] for r in data), default=None),
        periods=list(PERIOD_COLUMNS), excluded_suspicious=sum(r['suspicious'] for r in data),
        rows=[dict(kind=kind, currency=currency,
            **{k: r[k] for k in ('run','serie','name','admin','category','category_type','category_group','date')},
            values={period: finite(r[column]) for period, column in PERIOD_COLUMNS.items()}) for r in valid])


@router.get('/returns')
def returns(kind: Literal['fm','fi']='fm',
            currency: str=Query('CLP',pattern='^(?:[A-Z]{3}|UF)$')):
    return load_returns(kind,currency,classification_cache_epoch())


@lru_cache(maxsize=32)
def load_nnm(kind, currency, period, start, end, bucket):
    from .router import rows
    if end is None:
        result=rows(f"""SELECT MAX(latest_data_date) date FROM mv_industry_monthly_{kind}
            WHERE currency=:currency AND latest_data_date<=CURRENT_DATE""",dict(currency=currency))
        end=result[0]['date'] if result else None
    if end is None:
        return dict(kind=kind,currency=currency,start=None,end=None,last_date=None,rows=[],
                    methodology='nav_implied' if kind=='fi' else 'reported_external')
    window=next(w for w in flow_windows(end) if w['key']==period)
    start=start or window['start']
    validate_range(start,end)
    data=flow_data(kind,currency,start,end)
    registry,name,active=(('fondo_mutuo','razon_social_administradora','f.fecha_termino_operaciones IS NULL')
                         if kind=='fm' else ('fondos_inversion','administrador','f.vigente IS TRUE'))
    meta={r['run']:r for r in rows(f"""SELECT f.run_fondo run,
        COALESCE(f.{name},'Sin administradora') admin,c.tipo category_type,c.grupo category_group
        FROM {registry} f LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,tipo,grupo
          FROM categoria_{kind}_effective WHERE periodo<=CURRENT_DATE
          ORDER BY run_fondo,periodo DESC) c USING(run_fondo) WHERE {active}""")}
    result=[]
    for row in data:
        reported,migrations=finite(row['reported']),finite(row['migrations'])
        result.append(dict(kind=kind,currency=currency,**meta[row['run']],name=row['name'],
            category=row['category'],reported=reported,migrations=migrations,
            net=reported-migrations if reported is not None and migrations is not None else None,
            observations=row['observations'],reported_observations=row['reported_observations'],
            first_date=row['first_date'],last_date=row['last_date'],
            excluded={k:row.get(k,0) for k in ('missing_base','gaps','series_changes','incomparable_nav')}))
    return dict(kind=kind,currency=currency,start=start,end=end,
        last_date=max((r['last_date'] for r in data),default=None),
        methodology='nav_implied' if kind=='fi' else 'reported_external',rows=result)


@router.get('/nnm')
def nnm(kind: Literal['fm','fi']='fm',currency: str=Query('CLP',pattern='^(?:[A-Z]{3}|UF)$'),
        period: Literal['1D','1W','1M','3M','6M','1A','5A','YTD']='1M',
        from_date: date|None=None,to_date: date|None=None):
    if from_date is not None and to_date is None:
        raise HTTPException(422,'El rango personalizado requiere ambas fechas.')
    if to_date is not None:
        validate_range(from_date or to_date,to_date)
    return load_nnm(kind,currency,period,from_date,to_date,classification_cache_epoch())
