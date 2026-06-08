from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler

from src.db.engine import SessionLocal
from src.db.models.job_runs import JobRun

logger = logging.getLogger(__name__)


def _run_tracked(job_id: str, fn):
    started_at = datetime.now(timezone.utc)
    run_id: int | None = None
    try:
        with SessionLocal() as s:
            run = JobRun(job_id=job_id, started_at=started_at, status="running")
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
        result = fn()
        _finish(run_id, "success",
                rows_upserted=getattr(result, "rows_upserted", None),
                errors=getattr(result, "errors", None))
        return result
    except Exception:
        _finish(run_id, "error", error_detail=traceback.format_exc())
        raise


def _finish(run_id: int | None, status: str, **kwargs) -> None:
    if run_id is None:
        return
    try:
        with SessionLocal() as s:
            run = s.get(JobRun, run_id)
            if run:
                run.finished_at = datetime.now(timezone.utc)
                run.status = status
                for k, v in kwargs.items():
                    setattr(run, k, v)
                s.commit()
    except Exception:
        logger.exception("Failed to persist job_run id=%s", run_id)


def _refresh_view(view: str):
    from sqlalchemy import text
    from src.base import DownloadResult
    with SessionLocal() as s:
        s.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
        s.commit()
    return DownloadResult(downloaded=1)


def _refresh_rentabilidad_fi():
    return _refresh_view("mv_rentabilidad_fi")


def _refresh_rentabilidad_fm():
    return _refresh_view("mv_rentabilidad_fm")


def _refresh_administradores():
    return _refresh_view("mv_administradores")


def register_jobs(scheduler: BaseScheduler) -> None:
    """Register all cron jobs on the given scheduler instance."""

    from src.mutualFunds.downloaders.bondsNemotecnicos import BonosNemotecnicosDownloader
    from src.mutualFunds.downloaders.tacDownloader import TacDownloader
    from src.mutualFunds.downloaders.carterasDownloader import CarterasDownloader
    from src.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
    from src.mutualFunds.downloaders.identificationDownloader import FMIdentidadDownloader
    from src.mutualFunds.downloaders.nemotecnicosDownloader import NemotecnicosDownloader
    from src.investmentFunds.downloaders.nemotecnicosDownloader import FINemotecnicosDownloader
    from src.investmentFunds.downloaders.identidadDownloader import FIIdentidadDownloader
    from src.investmentFunds.downloaders.valoresCuotaDownloader import ValoresCuotaFIDownloader
    from src.investmentFunds.downloaders.aportantesDownloader import AportantesDownloader
    from src.investmentFunds.downloaders.carterasDownloader import CarterasFIDownloader
    from src.bolsaSantiago.downloaders.dividendosDownloader import DividendosDownloader
    from src.investmentFunds.investmentFundsCategories import run_and_save as fi_categorize


    def _job(job_id: str, fn):
        def wrapper():
            logger.info("Starting: %s", job_id)
            result = _run_tracked(job_id, fn)
            logger.info("Done: %s — %s", job_id, result)
        wrapper.__name__ = job_id
        return wrapper

    scheduler.add_job(
        _job("bonds_tickers", lambda: BonosNemotecnicosDownloader(force=True).run()),
        "cron", hour=8, minute=0, id="bonds_tickers",
    )
    scheduler.add_job(
        _job("fm_identity", lambda: FMIdentidadDownloader(force=True).run()),
        "cron", hour=5, minute=42, id="fm_identity",
    )
    scheduler.add_job(
        _job("mf_tickers", lambda: NemotecnicosDownloader(force=True).run()),
        "cron", hour=8, minute=15, id="mf_tickers",
    )
    scheduler.add_job(
        _job("fi_tickers", lambda: FINemotecnicosDownloader(force=True).run()),
        "cron", hour=8, minute=15, id="fi_tickers",
    )
    scheduler.add_job(
        _job("fi_identity", lambda: FIIdentidadDownloader(force=True).run()),
        "cron", hour=8, minute=20, id="fi_identity",
    )
    scheduler.add_job(
        _job("mf_daily_nav", lambda: CartolaDownloader().run()),
        "cron", hour=8, minute=30, id="mf_daily_nav",
    )
    scheduler.add_job(
        _job("mf_rentabilidad", _refresh_rentabilidad_fm),
        "cron", hour=9, minute=15, id="mf_rentabilidad",
    )
    scheduler.add_job(
        _job("administradores", _refresh_administradores),
        "cron", hour=9, minute=20, id="administradores",
    )
    scheduler.add_job(
        _job("mf_portfolios", lambda: CarterasDownloader().run()),
        "cron", day=5, hour=9, minute=0, id="mf_portfolios",
    )
    scheduler.add_job(
        _job("mf_costs", lambda: TacDownloader().run()),
        "cron", day=5, hour=9, minute=30, id="mf_costs",
    )
    scheduler.add_job(
        _job("dividends", lambda: DividendosDownloader().run()),
        "cron", hour=9, minute=0, id="dividends",
    )
    scheduler.add_job(
        _job("fi_daily_nav", lambda: ValoresCuotaFIDownloader().run()),
        "cron", hour=9, minute=30, id="fi_daily_nav",
    )
    scheduler.add_job(
        _job("fi_rentabilidad", _refresh_rentabilidad_fi),
        "cron", hour=10, minute=0, id="fi_rentabilidad",
    )
    scheduler.add_job(
        _job("fi_shareholders", lambda: AportantesDownloader().run()),
        "cron", day=5, hour=10, minute=0, id="fi_shareholders",
    )
    scheduler.add_job(
        _job("fi_portfolios", lambda: CarterasFIDownloader().run()),
        "cron", day=5, hour=10, minute=30, id="fi_portfolios",
    )
    scheduler.add_job(
        _job("fi_categories", fi_categorize),
        "cron", day=5, hour=11, minute=0, id="fi_categories",
    )


def start() -> BackgroundScheduler:
    """Start a background scheduler — used only for local development via main.py."""
    scheduler = BackgroundScheduler(timezone="America/Santiago")
    register_jobs(scheduler)
    scheduler.start()
    logger.info("Scheduler started (background) — timezone: America/Santiago")
    return scheduler
