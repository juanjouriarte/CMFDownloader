"""Resumable local CMF quarterly equity history import; never starts the scheduler.

PYTHONPATH=. .venv/bin/python scripts/backfill_fi_reported_equity.py --from-date 2019-12-31
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import logging
from pathlib import Path
from threading import Lock

from src.db.engine import engine
from src.etl.investmentFunds.reported_equity import ReportedEquityDownloader


def missing_from_journal(path):
    latest = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                item = json.loads(line)
                latest[(item['run'],date.fromisoformat(item['period']))] = item['status']
            except (ValueError,KeyError):
                # A truncated final line after interruption must be rechecked.
                continue
    return {key for key,status in latest.items() if status in ('unavailable','resumed_missing')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-date',type=date.fromisoformat,required=True)
    parser.add_argument('--to-date',type=date.fromisoformat)
    parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
    parser.add_argument('--run',action='append',help='Restrict to an eligible fund; repeatable')
    parser.add_argument('--retry-missing',action='store_true',help='Recheck explicitly unavailable reports')
    parser.add_argument('--journal',type=Path,default=Path('output/data-audit/fi-equity-history.jsonl'))
    args=parser.parse_args()
    if engine.url.host not in ('localhost','127.0.0.1','::1'):
        parser.error('This backfill command only writes to the local database')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    args.journal.parent.mkdir(parents=True,exist_ok=True)
    completed=set() if args.retry_missing else missing_from_journal(args.journal)
    counts=Counter();lock=Lock()
    with args.journal.open('a',buffering=1) as journal:
        journal.write('\n')  # Separate a previously truncated final record.
        def progress(item):
            with lock:
                item['checked_at']=datetime.now(timezone.utc).isoformat()
                journal.write(json.dumps(item,ensure_ascii=False)+'\n')
                counts[item['status']]+=1
                if sum(counts.values()) % 25 == 0:
                    logging.info('Progress: %s; latest %s %s',dict(counts),item['run'],item['period'])
        result=ReportedEquityDownloader().run(args.run,from_date=args.from_date,to_date=args.to_date,
            skip_existing=True,completed_missing=completed,on_result=progress,workers=args.workers)
    print(json.dumps(dict(result=asdict(result),outcomes=dict(counts),journal=str(args.journal))),flush=True)
    return 1 if result.errors else 0


if __name__=='__main__':
    raise SystemExit(main())
