from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

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
from src.bolsaSantiago.downloaders.dividendosDownloader import DividendosDownloader

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/Santiago")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="bonds_tickers")
def job_bonds_tickers() -> None:
    logger.info("Starting download: Bond Tickers")
    result = BonosNemotecnicosDownloader(force=True).run()
    logger.info("Bond Tickers done: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=0, id="fm_identity")
def job_fm_identity() -> None:
    logger.info("Starting download: MF Identity")
    result = FMIdentidadDownloader(force=True).run()
    logger.info("MF Identity done: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=15, id="mf_tickers")
def job_mf_tickers() -> None:
    logger.info("Starting download: MF Tickers")
    result = NemotecnicosDownloader(force=True).run()
    logger.info("MF Tickers done: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=15, id="fi_tickers")
def job_fi_tickers() -> None:
    logger.info("Starting download: FI Tickers")
    result = FINemotecnicosDownloader(force=True).run()
    logger.info("FI Tickers done: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=20, id="fi_identity")
def job_fi_identity() -> None:
    logger.info("Starting download: FI Identity")
    result = FIIdentidadDownloader(force=True).run()
    logger.info("FI Identity done: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=30, id="mf_daily_nav")
def job_mf_daily_nav() -> None:
    logger.info("Starting download: MF Daily NAV")
    result = CartolaDownloader().run()
    logger.info("MF Daily NAV done: %s", result)


@scheduler.scheduled_job("cron", day=5, hour=9, minute=0, id="mf_portfolios")
def job_mf_portfolios() -> None:
    logger.info("Starting download: MF Portfolios")
    result = CarterasDownloader().run()
    logger.info("MF Portfolios done: %s", result)


@scheduler.scheduled_job("cron", day=5, hour=9, minute=30, id="mf_costs")
def job_mf_costs() -> None:
    logger.info("Starting download: MF Costs (TAC)")
    result = TacDownloader().run()
    logger.info("MF Costs done: %s", result)


@scheduler.scheduled_job("cron", hour=9, minute=0, id="dividends")
def job_dividends() -> None:
    logger.info("Starting download: Dividends")
    result = DividendosDownloader().run()
    logger.info("Dividends done: %s", result)


@scheduler.scheduled_job("cron", hour=9, minute=30, id="fi_daily_nav")
def job_fi_daily_nav() -> None:
    logger.info("Starting download: FI Daily NAV")
    result = ValoresCuotaFIDownloader().run()
    logger.info("FI Daily NAV done: %s", result)


@scheduler.scheduled_job("cron", day=5, hour=10, minute=0, id="fi_shareholders")
def job_fi_shareholders() -> None:
    logger.info("Starting download: FI Shareholders & Shares")
    result = AportantesDownloader().run()
    logger.info("FI Shareholders done: %s", result)


def start() -> None:
    logger.info("Scheduler started — timezone: America/Santiago")
    scheduler.start()
    # BackgroundScheduler runs in a daemon thread — caller is responsible
    # for keeping the process alive (e.g. uvicorn serving FastAPI)
