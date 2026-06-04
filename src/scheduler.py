from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from src.mutualFunds.cartolaDownloader import CartolaDownloader
from src.mutualFunds.identificationDownloader import FMIdentidadDownloader

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/Santiago")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="fm_identidad")
def job_fm_identidad() -> None:
    logger.info("Iniciando descarga: FM Identidad")
    result = FMIdentidadDownloader(force=True).run()
    logger.info("FM Identidad finalizada: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=30, id="cartola_diaria")
def job_cartola_diaria() -> None:
    logger.info("Iniciando descarga: Cartola Diaria")
    result = CartolaDownloader().run()
    logger.info("Cartola Diaria finalizada: %s", result)


def start() -> None:
    logger.info("Scheduler iniciado — timezone: America/Santiago")
    scheduler.start()
    # BackgroundScheduler runs in a daemon thread — caller is responsible
    # for keeping the process alive (e.g. uvicorn serving FastAPI)
