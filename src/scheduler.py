from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from src.mutualFunds.downloaders.bondsNemotecnicos import BonosNemotecnicosDownloader
from src.mutualFunds.downloaders.tacDownloader import TacDownloader
from src.mutualFunds.downloaders.carterasDownloader import CarterasDownloader
from src.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
from src.mutualFunds.downloaders.identificationDownloader import FMIdentidadDownloader
from src.mutualFunds.downloaders.nemotecnicosDownloader import NemotecnicosDownloader

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/Santiago")


@scheduler.scheduled_job("cron", hour=8, minute=0, id="bonos_nemotecnicos")
def job_bonos_nemotecnicos() -> None:
    logger.info("Iniciando descarga: Bonos Nemotécnicos")
    result = BonosNemotecnicosDownloader(force=True).run()
    logger.info("Bonos Nemotécnicos finalizada: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=0, id="fm_identidad")
def job_fm_identidad() -> None:
    logger.info("Iniciando descarga: FM Identidad")
    result = FMIdentidadDownloader(force=True).run()
    logger.info("FM Identidad finalizada: %s", result)


@scheduler.scheduled_job("cron", day=5, hour=9, minute=30, id="tac")
def job_tac() -> None:
    logger.info("Iniciando descarga: TAC")
    result = TacDownloader().run()
    logger.info("TAC finalizada: %s", result)


@scheduler.scheduled_job("cron", day=5, hour=9, minute=0, id="carteras")
def job_carteras() -> None:
    logger.info("Iniciando descarga: Carteras FM")
    result = CarterasDownloader().run()
    logger.info("Carteras FM finalizada: %s", result)


@scheduler.scheduled_job("cron", hour=8, minute=15, id="nemotecnicos")
def job_nemotecnicos() -> None:
    logger.info("Iniciando descarga: Nemotécnicos FM")
    result = NemotecnicosDownloader(force=True).run()
    logger.info("Nemotécnicos FM finalizada: %s", result)


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
