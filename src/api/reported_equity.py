"""Quarterly net-equity fallback for non-rescatable FI without daily NAV."""
from datetime import date, timedelta
from fastapi import APIRouter, HTTPException
from .deps import CacheHook
from .industry import _rows, _reference
from src.etl.investmentFunds.reported_equity import ELIGIBLE_SQL

MAX_REPORT_AGE_DAYS = 185
router = APIRouter(tags=['Investment funds'])


def reported_fund_directory():
    """Opt-in research directory; keep stale/negative statements visible and labelled.
    Never add these rows to NAV rankings or treat them as daily observations.
    """
    return _rows('''WITH eligible AS ('''+ELIGIBLE_SQL+''')
      SELECT 'fi' kind,f.run_fondo run,f.razon_social name,f.administrador admin,
        c.nombre_cat category,c.tipo category_type,c.grupo category_group,
        r.currency,r.equity aum,r.periodo data_date,'quarterly_report' aum_source
      FROM eligible e JOIN fondos_inversion f USING(run_fondo)
      JOIN LATERAL (SELECT * FROM fi_reported_equity r WHERE r.run_fondo=f.run_fondo
        AND periodo<=:today ORDER BY periodo DESC LIMIT 1) r ON true
      LEFT JOIN LATERAL (SELECT * FROM categoria_fi_effective c
        WHERE c.run_fondo=f.run_fondo AND periodo<=:today ORDER BY periodo DESC LIMIT 1) c ON true
      ORDER BY r.currency,r.equity DESC''',dict(today=date.today()))


@router.get('/investment-funds/{run}/financial-statements')
def financial_statements(run: str, _: CacheHook):
    fund = _rows('''SELECT run_fondo, vigente, rescatable,
      EXISTS(SELECT 1 FROM valores_cuota_fi v WHERE v.run_fondo=f.run_fondo
        AND fecha<=CURRENT_DATE AND patrimonio_neto>=0
        AND patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity')) has_nav
      FROM fondos_inversion f WHERE run_fondo=:run''',dict(run=run))
    if not fund:
        raise HTTPException(404,'Investment fund not found')
    reports = _rows('''SELECT periodo,currency,equity,assets,liabilities,source_url,fetched_at
      FROM fi_reported_equity WHERE run_fondo=:run AND periodo<=CURRENT_DATE
      ORDER BY periodo''',dict(run=run))
    cutoff = _rows(f'SELECT ({_reference("valores_cuota_fi")}) cutoff')[0]['cutoff']
    candidate = dict(run_fondo=run,name='',currency=None)
    latest = next((r for r in reversed(reports) if cutoff and r['periodo']<=cutoff),None)
    selected,excluded = select_reports([candidate],{run:latest} if latest else {},cutoff) if cutoff else ([],[])
    eligible = fund[0]['vigente'] and fund[0]['rescatable'] is False and not fund[0]['has_nav']
    return dict(run=run,has_nav=fund[0]['has_nav'],reports=reports,as_of=cutoff,
                max_age_days=MAX_REPORT_AGE_DAYS,included_in_panorama=bool(eligible and selected),
                panorama_report_date=latest['periodo'] if eligible and selected else None,
                exclusion_reason=('not_eligible' if not eligible else excluded[0]['reason'] if excluded else
                                  'missing_reference_date' if not cutoff else None))


def select_reports(candidates, reports, cutoff):
    """A whole-fund fallback only; never fill an absent series or overwrite NAV."""
    selected, excluded = [], []
    for original in candidates:
        report = reports.get(original['run_fondo'])
        reason = ('missing_report' if not report else
                  'future_report' if report['periodo'] > cutoff else
                  'stale_report' if report['periodo'] < cutoff-timedelta(days=MAX_REPORT_AGE_DAYS) else
                  'invalid_equity' if not report['equity'].is_finite() or report['equity'] < 0 else None)
        if reason:
            excluded.append(dict(run_fondo=original['run_fondo'],name=original['name'],reason=reason,
                                 currency=report['currency'] if report else original.get('currency'),
                                 date=report['periodo'] if report else None))
            continue
        selected.append(dict(original,currency=report['currency'],aum=report['equity'],
                             data_date=report['periodo'],aum_source='quarterly_report',
                             source_url=report['source_url']))
    return selected,excluded


def supplement(snapshot, cutoff):
    runs=[r['run_fondo'] for r in snapshot if r['fund_type']=='fi' and r['aum'] is None]
    if not runs or cutoff is None:
        return [],[]
    eligible = _rows(ELIGIBLE_SQL,dict(today=cutoff))
    eligible={r['run_fondo'] for r in eligible}
    candidates=[r for r in snapshot if r['fund_type']=='fi' and r['run_fondo'] in eligible and r['aum'] is None]
    reports=_rows('''SELECT DISTINCT ON(run_fondo) * FROM fi_reported_equity
      WHERE run_fondo=ANY(:runs) AND periodo<=:cutoff ORDER BY run_fondo,periodo DESC''',
      dict(runs=list(eligible),cutoff=cutoff)) if eligible else []
    return select_reports(candidates,{r['run_fondo']:r for r in reports},cutoff)
