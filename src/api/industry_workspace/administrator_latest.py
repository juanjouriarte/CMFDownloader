"""Latest administrator universe uses Panorama's NAV and EEFF selection verbatim."""
from datetime import date

from src.api.industry import _snapshot_cte, _filters, _reference, _rows
from src.api.reported_equity import supplement


def snapshot(kind, source):
    params = dict(fund_type=kind, currency=None, admin=None, nombre_cat=None, vigente=True)
    universe = _rows(f'{_snapshot_cte(include_returns=False, include_ytd=False)} '
                     f'SELECT * FROM snapshot {_filters(params)}', params)
    report_cutoff = _rows(f'SELECT ({_reference("valores_cuota_fi")}) cutoff')[0]['cutoff']
    nav = [dict(r, aum_source='nav') for r in universe if r['aum'] is not None]
    references = {k: max((r['data_date'] for r in nav if r['fund_type']==k), default=None)
                  for k in ('fm', 'fi')}
    added, excluded = supplement(universe, report_cutoff) if source != 'nav' else ([], [])
    selected = (nav if source != 'quarterly_report' else []) + added
    cutoff = max((r['data_date'] for r in nav), default=report_cutoff or date.today())
    month = cutoff.replace(day=1)
    rows = [dict(kind=r['fund_type'], run_fondo=r['run_fondo'], fund_name=r['name'],
                 admin=r['administrator'] or 'Sin administradora', category=r['category_name'] or 'Sin clasificación',
                 month=month, latest_data_date=cutoff, valuation_date=r['data_date'], currency=r['currency'],
                 aum=r['aum'], aum_source=r['aum_source'], source_url=r.get('source_url')) for r in selected]
    by_run = {r['run_fondo']:r for r in universe if r['fund_type']=='fi'}
    excluded = [dict(r, admin=by_run[r['run_fondo']]['administrator'],
                     category=by_run[r['run_fondo']]['category_name'] or 'Sin clasificación') for r in excluded]
    return dict(date=cutoff, rows=rows, excluded=excluded, reference_dates=references)
