from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from src.mutualFunds.identificationDownloader import FMIdentidadDownloader

logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="America/Santiago")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="fm_identidad")
def job_fm_identidad() -> None:
    logger.info("Iniciando descarga: FM Identidad")
    result = FMIdentidadDownloader(force=True).run()
    logger.info("FM Identidad finalizada: %s", result)


def start() -> None:
    logger.info("Scheduler iniciado — timezone: America/Santiago")
    scheduler.start()
