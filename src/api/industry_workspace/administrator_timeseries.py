"""Exact-date NAV history and reported external FM flows for an AGF."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
from time import monotonic
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from src.api.industry import FM_CURRENCY, FI_CURRENCY

router = APIRouter(prefix='/administrators')


def validate_range(start, end):
    if start < date(2020, 1, 1) or start > end or end > date.today():
        raise HTTPException(422, 'Seleccione fechas entre 2020 y hoy, en orden cronológico.')


@lru_cache(maxsize=32)
def load_history(currency, kind, admin, start, end, category, metric, bucket):
    from .router import rows
    parts = []
    for k, table, fund_table, alias, name, active, expression in [
        ('fm', 'cartola_diaria', 'fondo_mutuo', 'cd', 'razon_social_administradora', 'f.fecha_termino_operaciones IS NULL', FM_CURRENCY),
        ('fi', 'valores_cuota_fi', 'fondos_inversion', 'v', 'administrador', 'f.vigente IS TRUE', FI_CURRENCY),
    ]:
        if kind not in ('all', k):
            continue
        parts.append(f"""SELECT '{k}' kind, {alias}.run_fondo, {alias}.fecha date,
            COALESCE(f.{name}, 'Sin administradora') admin, SUM({alias}.patrimonio_neto) aum
            FROM {table} {alias} JOIN {fund_table} f USING(run_fondo)
            LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo, nombre_cat FROM categoria_{k}
              WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
            WHERE {alias}.fecha BETWEEN :start AND :end AND {expression}=:currency AND {active}
              AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)
              {'AND COALESCE(f.'+name+", 'Sin administradora')=:admin" if metric == 'aum' else ''}
              AND {alias}.patrimonio_neto IS NOT NULL
              AND {alias}.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')
              AND {alias}.patrimonio_neto>=0
            GROUP BY {alias}.run_fondo,{alias}.fecha,f.{name}""")
    data = rows('WITH daily AS ('+' UNION ALL '.join(parts)+''')
        SELECT date, SUM(aum) FILTER(WHERE admin=:admin) aum,
          COUNT(*) FILTER(WHERE admin=:admin) funds,
          SUM(aum) market_aum, COUNT(*) market_funds
        FROM daily GROUP BY date ORDER BY date''',
        dict(currency=currency, start=start, end=end, admin=admin, category=category))
    granularity = 'daily' if (end-start).days <= 400 else 'monthly'
    if granularity == 'monthly':
        # Last observed common day per calendar month, never a fabricated month-end.
        latest = {r['date'].replace(day=1): r for r in data}
        data = list(latest.values())
    points = [dict(date=r['date'], aum=float(r['aum']) if r['aum'] is not None else None,
        share=float(r['aum']/r['market_aum']*100) if metric=='share' and r['aum'] is not None and r['market_aum'] else None,
        funds=r['funds'], market_funds=r['market_funds'] if metric=='share' else None) for r in data]
    return dict(currency=currency, kind=kind, admin=admin, start=start, end=end,
        granularity=granularity, points=points)


@router.get('/history')
def administrator_history(admin: str=Query(min_length=1,max_length=300),
        from_date: date=Query(), to_date: date=Query(),
        currency: str=Query('CLP',pattern='^(?:[A-Z]{3}|UF)$'),
        kind: Literal['all','fm','fi']='all', category: str|None=None,
        metric: Literal['aum','share']='aum'):
    validate_range(from_date,to_date)
    return load_history(currency,kind,admin,from_date,to_date,category,metric,int(monotonic()//60))


def flow_tree(funds):
    """Every category and total sums its displayed children; missing stays missing."""
    def totals(items):
        known = [r for r in items if r['reported'] is not None]
        return dict(**{k:sum(float(r[k]) for r in known) if known else None
                     for k in ('aportes','rescates','reported','migrations','net')},
            funds=len(items), reported_funds=len(known),
            observations=sum(r['observations'] for r in items),
            reported_observations=sum(r['reported_observations'] for r in items))
    categories=defaultdict(list)
    for r in funds:
        row=dict(r)
        for key in ('aportes','rescates','reported','migrations'):
            row[key]=float(row[key]) if row[key] is not None else None
        row['net']=row['reported']-row['migrations'] if row['reported'] is not None else None
        categories[row['category']].append(row)
    groups=[]
    for name,children in categories.items():
        children.sort(key=lambda r:(r['net'] is None,-(r['net'] or 0),r['name']))
        groups.append(dict(name=name,children=children,**totals(children)))
    groups.sort(key=lambda r:(r['net'] is None,-(r['net'] or 0),r['name']))
    return dict(categories=groups,total=totals([f for g in groups for f in g['children']]))


@lru_cache(maxsize=32)
def load_flows(currency, admin, start, end, category, bucket):
    from .router import rows
    data=rows(f"""WITH observations AS MATERIALIZED (
        SELECT cd.run_fondo, cd.fecha, f.nombre_fondo name,
          COALESCE(c.nombre_cat,'Sin clasificación') category, cd.patrimonio_neto,
          CASE WHEN cd.monto_aportado::text NOT IN ('NaN','Infinity','-Infinity')
                 AND cd.monto_rescatado::text NOT IN ('NaN','Infinity','-Infinity')
                 AND cd.monto_aportado>=0 AND cd.monto_rescatado>=0
               THEN cd.monto_aportado END aportes,
          CASE WHEN cd.monto_aportado::text NOT IN ('NaN','Infinity','-Infinity')
                 AND cd.monto_rescatado::text NOT IN ('NaN','Infinity','-Infinity')
                 AND cd.monto_aportado>=0 AND cd.monto_rescatado>=0
               THEN cd.monto_rescatado END rescates
        FROM cartola_diaria cd JOIN fondo_mutuo f USING(run_fondo)
        LEFT JOIN (SELECT DISTINCT ON(run_fondo) run_fondo,nombre_cat FROM categoria_fm
          WHERE periodo<=CURRENT_DATE ORDER BY run_fondo,periodo DESC) c USING(run_fondo)
        WHERE cd.fecha BETWEEN :start AND :end AND {FM_CURRENCY}=:currency
          AND COALESCE(f.razon_social_administradora,'Sin administradora')=:admin
          AND f.fecha_termino_operaciones IS NULL
          AND (:category IS NULL OR COALESCE(c.nombre_cat,'Sin clasificación')=:category)
    ), adjustments AS (
        SELECT a.target_run_fondo run_fondo,SUM(a.amount) amount FROM fm_flow_adjustments a
        WHERE a.currency=:currency AND a.status IN ('auto_confirmed','confirmed')
          AND a.event_date BETWEEN :start AND :end
          AND EXISTS(SELECT 1 FROM observations o WHERE o.run_fondo=a.target_run_fondo
                     AND o.fecha=a.event_date AND o.aportes IS NOT NULL)
        GROUP BY a.target_run_fondo
    ) SELECT o.run_fondo run, o.name, o.category, MIN(fecha) first_date, MAX(fecha) last_date,
        SUM(aportes) aportes, SUM(rescates) rescates, SUM(aportes-rescates) reported,
        CASE WHEN COUNT(aportes)>0 THEN COALESCE(MAX(a.amount),0) END migrations,
        COUNT(*) observations, COUNT(aportes) reported_observations
      FROM observations o LEFT JOIN adjustments a USING(run_fondo)
      GROUP BY o.run_fondo,o.name,o.category
      HAVING COUNT(*) FILTER(WHERE patrimonio_neto IS NOT NULL AND patrimonio_neto>=0
        AND patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity'))>0""",
      dict(currency=currency,admin=admin,start=start,end=end,category=category))
    dates=[r['last_date'] for r in data]
    return dict(currency=currency,kind='fm',admin=admin,start=start,end=end,
        last_date=max(dates) if dates else None,**flow_tree(data))


@router.get('/flows')
def administrator_flows(admin: str=Query(min_length=1,max_length=300),
        from_date: date=Query(), to_date: date=Query(),
        currency: str=Query('CLP',pattern='^(?:[A-Z]{3}|UF)$'), category: str|None=None):
    validate_range(from_date,to_date)
    return load_flows(currency,admin,from_date,to_date,category,int(monotonic()//60))
