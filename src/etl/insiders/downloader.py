from __future__ import annotations

import argparse
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import hashlib
import logging
from time import sleep
from zoneinfo import ZoneInfo

from sqlalchemy import text
from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.http import make_session
from .loader import parse_html, load_window

URL = 'https://www.cmfchile.cl/institucional/inc/tra/art_12_val.php'
PAGE_URL = 'https://www.cmfchile.cl/institucional/estadisticas/valores_satra_art12v.php'
HISTORY_START = date(2020, 1, 1)
LOOKBACK_DAYS = 100


def today_chile():
    return datetime.now(ZoneInfo('America/Santiago')).date()


def month_windows(start: date, end: date):
    while start <= end:
        last = min(end, date(start.year, start.month, monthrange(start.year, start.month)[1]))
        yield start, last
        start = last + timedelta(days=1)


class InsidersDownloader(BaseDownloader):
    def __init__(self):
        super().__init__(DOWNLOADS_DIR / 'insiders')

    def fetch_window(self, start, end):
        records, sources = [], []
        with make_session(headers={'Referer': PAGE_URL}) as session:
            for status in ('VI', 'NV'):
                fetched_at = datetime.now(timezone.utc).isoformat()
                response = session.get(URL, params=dict(soc='', desde=start.strftime('%d/%m/%Y'),
                    hasta=end.strftime('%d/%m/%Y'), dias='', vig=status), timeout=(15,90))
                response.raise_for_status()
                # Source declares UTF-8. Decode strictly; never silently corrupt names.
                html = response.content.decode('utf-8-sig')
                parsed = parse_html(html, status, start, end)
                records.extend(parsed)
                sources.append(dict(status=status,url=response.url,row_count=len(parsed),
                    sha256=hashlib.sha256(response.content).hexdigest(),bytes=len(response.content),fetched_at=fetched_at))
                sleep(.25)
        return records, sources

    def _load(self, windows):
        total = DownloadResult()
        for start, end in windows:
            self.logger.info('Insiders %s through %s: fetching VI + NV', start, end)
            # Fail the tracked job on ANY malformed/failed response. Earlier months
            # remain committed; the failed month retains its previous snapshot.
            records, sources = self.fetch_window(start, end)
            rows = load_window(start, end, records, sources)
            total += DownloadResult(downloaded=2, rows_upserted=rows)
            self.logger.info('Insiders %s: %s rows loaded (%s)', start, rows,
                             ', '.join(f"{s['status']}={s['row_count']}" for s in sources))
        return total

    def backfill(self, from_date=HISTORY_START, to_date=None):
        end = to_date or today_chile()
        if from_date > end:
            raise ValueError('Start must not be after end')
        return self._load(month_windows(from_date, end))

    def run(self):
        today = today_chile()
        # Every first day: rescan the full history for older amendments and
        # issuer status changes. Otherwise recheck recent months + any missing
        # historical months, so a long worker outage cannot strand a gap.
        if today.day == 1:
            return self.backfill(HISTORY_START, today)
        recent = (today - timedelta(days=LOOKBACK_DAYS-1)).replace(day=1)
        with SessionLocal() as session:
            covered = set(session.execute(text('''SELECT DISTINCT date_from,date_to
                FROM insider_imports WHERE date_from>=:start'''), dict(start=HISTORY_START)).all())
        windows = [(a,b) for a,b in month_windows(HISTORY_START,today) if a>=recent or (a,b) not in covered]
        return self._load(windows)


def main():
    parser = argparse.ArgumentParser(description='Import CMF Article 12 disclosures into the local DB')
    parser.add_argument('--since', type=date.fromisoformat, default=HISTORY_START)
    parser.add_argument('--until', type=date.fromisoformat, default=None)
    parser.add_argument('--daily', action='store_true')
    args = parser.parse_args()
    from src.db.engine import engine
    if engine.url.host not in ('localhost','127.0.0.1','::1'):
        parser.error('This manual CLI is local-only; the deployed worker uses run().')
    from src.scheduler import _run_tracked
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    downloader = InsidersDownloader()
    result = _run_tracked('insiders_transactions',downloader.run if args.daily else lambda:downloader.backfill(args.since,args.until))
    print(result)


if __name__ == '__main__':
    main()
