from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from apscheduler.schedulers.blocking import BlockingScheduler

from src.scheduler import register_jobs

scheduler = BlockingScheduler(timezone="America/Santiago")
register_jobs(scheduler)

if __name__ == "__main__":
    logging.getLogger(__name__).info("Worker started — timezone: America/Santiago")
    scheduler.start()
