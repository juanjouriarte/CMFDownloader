"""Reload local fund datasets from an explicit date using the normal loaders/jobs.

PYTHONPATH=. .venv/bin/python scripts/reload_local_data.py --since 2026-07-01
Never starts the scheduler or sends the scheduled email reports.
"""
from __future__ import annotations

import argparse
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from threading import Lock
from time import monotonic

from requests import RequestException
from sqlalchemy import text
from src.base import DownloadResult
from src.db.engine import engine, SessionLocal
from src.scheduler import _run_tracked, _refresh_view
from src.etl.investmentFunds.downloaders.valoresCuotaDownloader import ValoresCuotaFIDownloader

logger = logging.getLogger('local_reload')


def fm_nav_backfill(since, today):
    """Bound CMF exports to a month; split interrupted transfers into weeks."""
    from src.etl.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
    downloader = CartolaDownloader(force=True)
    def download(start, end):
        try:
            return downloader._download_ranges([(start, end)])
        except RequestException:
            if (end-start).days <= 7:
                raise
            middle = start + (end-start)//2
            logger.warning('CMF transfer interrupted; splitting %s through %s', start, end)
            return download(start,middle) + download(middle+timedelta(days=1),end)
    result = DownloadResult()
    while since <= today:
        end = min(today,date(since.year,since.month,monthrange(since.year,since.month)[1]))
        result += download(since,end)
        since = end + timedelta(days=1)
    return result


class NavPartition(ValoresCuotaFIDownloader):
    """Disjoint fund batches reuse the normal fetch/load/retry implementation."""
    def __init__(self, funds):
        super().__init__(force=True)
        self.funds = funds

    def _get_funds(self, only_vigentes):
        return self.funds


def nav_backfill(since, today, workers):
    funds = ValoresCuotaFIDownloader()._get_funds(False)
    logger.info('FI NAV: %d funds, %d workers, %s through %s', len(funds), workers, since, today)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(
            lambda batch: NavPartition(batch)._download_all(since, today, only_vigentes=False),
            [funds[i::workers] for i in range(workers)],
        )
        total = DownloadResult()
        for result in results:
            total += result
        return total


def snapshot():
    with SessionLocal() as s:
        return {t: str(s.execute(text(f'SELECT max({col}) FROM {t}')).scalar()) for t, col in [
            ('cartola_diaria','fecha'), ('valores_cuota_fi','fecha'), ('cartera_naci','periodo'),
            ('cartera_fi_nac','periodo'), ('aportantes_fi','periodo'), ('cuotas_fi','periodo'),
            ('tac','periodo'), ('categoria_fm','periodo'), ('categoria_fi','periodo')]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', type=date.fromisoformat, required=True)
    parser.add_argument('--nav-workers', type=int, choices=range(1,5), default=4)
    parser.add_argument('--only', choices=['all','fm','fm_nav','fi_nav','fi_reports','derived'], default='all')
    args = parser.parse_args()
    if engine.url.host not in ('127.0.0.1','localhost','::1'):
        parser.error('This command only permits a local database host.')
    today = date.today()
    if args.since > today:
        parser.error('--since cannot be in the future')
    folder = Path('output/local-refresh')
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    report_file = folder / f'reload-{stamp}.json'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(folder / f'reload-{stamp}.log')])
    report = dict(since=str(args.since), through=str(today), before=snapshot(), jobs={},
                  started_at=datetime.now(timezone.utc).isoformat())
    lock = Lock()

    def save():
        report_file.write_text(json.dumps(report, default=str, indent=2))

    def job(name, fn):
        started = monotonic()
        logger.info('START %s', name)
        try:
            def checked():
                result = fn()
                if getattr(result, 'errors', 0):
                    raise RuntimeError(f'{name}: {result.errors} downloader errors; {result.rows_upserted} rows loaded')
                return result
            result = _run_tracked(name, checked)
            data = dict(status='success', seconds=round(monotonic()-started,1),
                        rows_upserted=getattr(result,'rows_upserted',result if isinstance(result,int) else None))
        except Exception as exc:
            logger.exception('FAILED %s', name)
            data = dict(status='error', seconds=round(monotonic()-started,1), error=str(exc))
        with lock:
            report['jobs'][name] = data
            save()
        logger.info('DONE %s %s', name, data)
        return data['status'] == 'success'

    save()
    if args.only == 'all':
        from src.etl.cmf.ref_codes import run as refs
        from src.etl.mutualFunds.downloaders.bondsNemotecnicos import BonosNemotecnicosDownloader
        from src.etl.mutualFunds.downloaders.identificationDownloader import FMIdentidadDownloader
        from src.etl.mutualFunds.downloaders.nemotecnicosDownloader import NemotecnicosDownloader
        from src.etl.investmentFunds.downloaders.nemotecnicosDownloader import FINemotecnicosDownloader
        from src.etl.investmentFunds.downloaders.identidadDownloader import FIIdentidadDownloader
        for name, fn in [('ref_codes_refresh',refs),('bonds_tickers',BonosNemotecnicosDownloader(force=True).run),
                         ('fm_identity',FMIdentidadDownloader(force=True).run),('mf_tickers',NemotecnicosDownloader(force=True).run),
                         ('fi_tickers',FINemotecnicosDownloader(force=True).run),('fi_identity',FIIdentidadDownloader(force=True).run)]:
            job(name,fn)

    def fm():
        from src.etl.mutualFunds.downloaders.carterasDownloader import CarterasDownloader
        from src.etl.mutualFunds.downloaders.tacDownloader import TacDownloader
        from src.etl.mutualFunds.mutualFundsCategories import run_and_save
        from src.etl.bolsaSantiago.downloaders.dividendosDownloader import DividendosDownloader
        job('mf_daily_nav',lambda: fm_nav_backfill(args.since,today))
        job('mf_portfolios',lambda: CarterasDownloader(force=True).backfill(args.since))
        job('mf_costs',lambda: TacDownloader(force=True).backfill(args.since))
        job('dividends',lambda: DividendosDownloader(force=True).backfill(args.since.year))
        def categories():
            with SessionLocal() as s:
                periods=s.execute(text('SELECT DISTINCT periodo FROM cartera_naci WHERE periodo>=:since AND periodo<=:today ORDER BY periodo'),dict(since=args.since,today=today)).scalars().all()
            return sum(run_and_save(p) for p in periods)
        job('mf_categories',categories)

    def fi_nav():
        job('fi_daily_nav',lambda: nav_backfill(args.since,today,args.nav_workers))

    def fi_reports():
        from src.etl.investmentFunds.downloaders.aportantesDownloader import AportantesDownloader
        from src.etl.investmentFunds.downloaders.carterasDownloader import CarterasFIDownloader
        from src.etl.investmentFunds.loaders.entidades import refresh_entidades
        from src.etl.investmentFunds.investmentFundsCategories import run_and_save
        def shareholders():
            result=AportantesDownloader(force=True)._download_all(args.since,today,only_vigentes=False,workers=2,fast=False)
            refresh_entidades()
            return result
        job('fi_shareholders',shareholders)
        job('fi_portfolios',lambda: CarterasFIDownloader(force=True)._download_all(args.since,today,only_vigentes=False,workers=2,fast=False))
        # Classify only available periods; retain the classifier's default
        # coverage threshold when selecting the most recent completed quarter.
        from src.etl.investmentFunds.investmentFundsCategories import latest_periodo
        def categories():
            with SessionLocal() as s:
                latest=latest_periodo()
                periods=s.execute(text('SELECT DISTINCT periodo FROM cartera_fi_nac WHERE periodo>=:since AND periodo<=:latest ORDER BY periodo'),dict(since=args.since,latest=latest)).scalars().all()
            return sum(run_and_save(p) for p in periods)
        job('fi_categories',categories)

    branches={'fm':fm,'fi_nav':fi_nav,'fi_reports':fi_reports}
    if args.only == 'fm_nav':
        job('mf_daily_nav',lambda: fm_nav_backfill(args.since,today))
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures=[executor.submit(fn) for name,fn in branches.items() if args.only in ('all',name)]
        for future in futures:
            future.result()

    from src.etl.mutualFunds.flow_adjustments import run as adjustments
    job('fm_flow_adjustments',adjustments)
    for kind in ('fm','fi'):
        for prefix in ('mv_nnm_daily','mv_industry_monthly','mv_rentabilidad'):
            view=f'{prefix}_{kind}'
            job(view.removeprefix('mv_'),lambda view=view:_refresh_view(view))
    job('administradores',lambda:_refresh_view('mv_administradores'))
    report.update(after=snapshot(),finished_at=datetime.now(timezone.utc).isoformat())
    save()
    logger.info('FINAL REPORT %s', report_file)
    raise SystemExit(1 if any(j['status']=='error' for j in report['jobs'].values()) else 0)


if __name__ == '__main__':
    main()
