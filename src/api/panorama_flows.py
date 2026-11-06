"""Monthly Panorama NNM from existing daily facts, with date-aligned FX."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException
from .deps import CacheHook
from .fx import Currency, month_ends
from .industry import _rows
from .classification_admin import classification_cache_epoch
from src.fx import Rates, MAX_AGE_DAYS

router=APIRouter(tags=['Industry'])


def monthly_flows(points,start,end,rates=None):
    grouped=defaultdict(list)
    for point in points:
        grouped[point['date'].replace(day=1)].append(point)
    result=[]
    for month,closing in month_ends(start,end):
        rows=grouped[month]
        amount=Decimal(0);reported=Decimal(0);migrations=Decimal(0);missing=0;valid=0
        for r in rows:
            if r['amount'] is None:
                continue
            rate=rates.get(r['currency'],r['date']) if rates is not None else {'clp_per_unit':Decimal(1)}
            if rate is None:
                missing+=1
                continue
            valid+=1
            factor=rate['clp_per_unit']
            amount+=r['amount']*factor
            reported+=r['reported']*factor
            migrations+=r['migrations']*factor
        observations=sum(r['observations'] for r in rows)
        usable=sum(r['valid_observations'] for r in rows)
        complete=bool(valid) and not missing
        result.append(dict(month=month,from_date=max(month,start),to_date=closing,
            amount=amount if complete else None,reported=reported if complete else None,
            migrations=migrations if complete else None,covered_amount=amount if valid else None,
            observations=observations,valid_observations=usable,missing_fx_points=missing,
            first_date=min((r['date'] for r in rows),default=None),last_date=max((r['date'] for r in rows),default=None),
            partial=closing.day<monthrange(closing.year,closing.month)[1],
            incomplete_data=usable<observations,
            state='missing_fx' if missing else 'no_data' if not rows else 'no_valid_data' if not valid else 'partial' if usable<observations else 'available'))
    return result


@lru_cache(maxsize=32)
def load_flows(kind,currency,start,end,admin,category,category_type,category_group,bucket):
    table='mv_nnm_daily_fm' if kind=='fm' else 'v_nnm_daily_fi_continuous'
    registry='fondo_mutuo' if kind=='fm' else 'fondos_inversion'
    administrator='razon_social_administradora' if kind=='fm' else 'administrador'
    active='f.fecha_termino_operaciones IS NULL' if kind=='fm' else 'f.vigente IS TRUE'
    cutoff=_rows(f'SELECT MAX(fecha) cutoff FROM {table} WHERE fecha<=CURRENT_DATE')[0]['cutoff']
    end=min(end,cutoff) if cutoff else end
    if end<start:
        return dict(kind=kind,currency=currency or 'CLP',mode='native' if currency else 'consolidated',
            from_date=start,to_date=end,cutoff=cutoff,points=[],max_fx_age_days=MAX_AGE_DAYS)
    params=dict(start=start,end=end,currency=currency,admin=f'%{admin}%' if admin else None,
        category=category,category_type=category_type,category_group=category_group)
    cte=f'''WITH eligible AS MATERIALIZED (
        SELECT f.run_fondo FROM {registry} f LEFT JOIN LATERAL (
            SELECT nombre_cat,tipo,grupo FROM categoria_{kind}_effective c
            WHERE c.run_fondo=f.run_fondo AND periodo<=CURRENT_DATE ORDER BY periodo DESC LIMIT 1
        ) c ON true WHERE {active}
        AND (:admin IS NULL OR f.{administrator} ILIKE :admin)
        AND (:category IS NULL OR COALESCE(c.nombre_cat,'')=:category)
        AND (:category_type IS NULL OR COALESCE(c.tipo,'')=:category_type)
        AND (:category_group IS NULL OR COALESCE(c.grupo,'')=:category_group)
    ), observations AS MATERIALIZED (
        SELECT d.*,BOOL_OR(has_nav) OVER(PARTITION BY run_fondo,currency,date_trunc('month',fecha)) month_has_nav
        FROM {table} d WHERE fecha BETWEEN :start AND :end
        AND (:currency IS NULL OR currency=:currency)
    )'''
    if kind=='fm':
        query=cte+''', adjustments AS (
            SELECT target_run_fondo run_fondo,event_date fecha,currency,SUM(amount) amount
            FROM fm_flow_adjustments WHERE event_date BETWEEN :start AND :end
                AND status IN ('confirmed','auto_confirmed') GROUP BY target_run_fondo,event_date,currency
        ) SELECT d.fecha date,d.currency,
            SUM(d.reported) reported,
            SUM(CASE WHEN d.reported_observations>0 THEN COALESCE(a.amount,0) ELSE 0 END) migrations,
            SUM(d.reported)-SUM(CASE WHEN d.reported_observations>0 THEN COALESCE(a.amount,0) ELSE 0 END) amount,
            SUM(d.observations) observations,SUM(d.reported_observations) valid_observations
        FROM observations d JOIN eligible f USING(run_fondo)
        LEFT JOIN adjustments a USING(run_fondo,fecha,currency)
        WHERE d.month_has_nav GROUP BY d.fecha,d.currency ORDER BY d.fecha'''
    else:
        valid="d.exclusion IS NULL AND d.previous_snapshot>=date_trunc('month',d.fecha)::date-7"
        query=cte+f''' SELECT d.fecha date,d.currency,
            SUM(d.estimate) FILTER(WHERE {valid}) reported,0::numeric migrations,
            SUM(d.estimate) FILTER(WHERE {valid}) amount,
            COUNT(*) observations,COUNT(*) FILTER(WHERE {valid}) valid_observations
        FROM observations d JOIN eligible f USING(run_fondo) WHERE d.month_has_nav
        GROUP BY d.fecha,d.currency ORDER BY d.fecha'''
    points=_rows(query,params)
    rates=Rates.load(start,end) if currency is None else None
    return dict(kind=kind,currency=currency or 'CLP',mode='native' if currency else 'consolidated',
        from_date=start,to_date=end,cutoff=cutoff,points=monthly_flows(points,start,end,rates),
        max_fx_age_days=MAX_AGE_DAYS)


@router.get('/industry/panorama/flows')
def panorama_flows(_:CacheHook,kind:Literal['fm','fi']='fm',currency:Currency=None,
        from_date:date=date(2020,1,1),to_date:date|None=None,admin:str|None=None,
        nombre_cat:str|None=None,category_type:str|None=None,category_group:str|None=None):
    start=from_date.replace(day=1)
    end=min(to_date or date.today(),date.today())
    if start<date(2020,1,1) or start>end:
        raise HTTPException(422,'Seleccione un período entre 2020 y hoy.')
    return load_flows(kind,currency,start,end,admin,nombre_cat,category_type,category_group,classification_cache_epoch())
