from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from sqlalchemy import text
from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from src.db.engine import SessionLocal
from src.http import make_session
from .parser import SOURCE_URL, parse_html

FIELDS = ('registration','run','fund_name','administrator','deposit_date','modified_date',
          'fund_type','active','liquidated','documents','approval_reference')


def load_snapshot(records, *, fetched_at, source_sha256, session=None):
    """Commit an entire validated snapshot atomically; preserve superseded documents."""
    if not records:
        raise ValueError('Empty registry snapshot')
    if session is None:
        with SessionLocal.begin() as db:
            return load_snapshot(records, fetched_at=fetched_at, source_sha256=source_sha256, session=db)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('cmf_fund_deposits'))"))
    previous = {(r['registration'],r['run']):dict(r) for r in session.execute(text('SELECT * FROM fund_deposits')).mappings()}
    previous_count = sum(r['source_present'] for r in previous.values())
    if previous_count and len(records) < previous_count * .8:
        raise ValueError('CMF snapshot lost over 20% of rows; previous data preserved')
    inserted = updated = 0
    seen = set()
    for record in records:
        key = (record['registration'],record['run'])
        if key in seen:
            raise ValueError('Duplicate registry identity')
        seen.add(key)
        old = previous.get(key)
        changed = old is None or any(old[f] != record[f] for f in FIELDS) or not old['source_present']
        if old is None: inserted += 1
        elif changed: updated += 1
        values = dict(record, documents=json.dumps(record['documents']), seen=fetched_at)
        if changed:
            session.execute(text('''INSERT INTO fund_deposits
                (registration,run,fund_name,administrator,deposit_date,modified_date,fund_type,active,liquidated,documents,approval_reference,first_seen_at,last_seen_at)
                VALUES (:registration,:run,:fund_name,:administrator,:deposit_date,:modified_date,:fund_type,:active,:liquidated,CAST(:documents AS jsonb),:approval_reference,:seen,:seen)
                ON CONFLICT (registration,run) DO UPDATE SET fund_name=EXCLUDED.fund_name,
                administrator=EXCLUDED.administrator,deposit_date=EXCLUDED.deposit_date,
                modified_date=EXCLUDED.modified_date,fund_type=EXCLUDED.fund_type,active=EXCLUDED.active,
                liquidated=EXCLUDED.liquidated,documents=EXCLUDED.documents,approval_reference=EXCLUDED.approval_reference,
                last_seen_at=EXCLUDED.last_seen_at,source_present=true'''), values)
            session.execute(text('''INSERT INTO fund_deposit_revisions(registration,run,observed_at,snapshot)
                VALUES (:registration,:run,:seen,CAST(:snapshot AS jsonb))'''),
                dict(registration=key[0],run=key[1],seen=fetched_at,snapshot=json.dumps(record,default=str)))
    # One update for all unchanged rows. Missing source rows are retained, not called liquidated.
    session.execute(text('CREATE TEMP TABLE seen_fund_deposits(registration TEXT,run TEXT) ON COMMIT DROP'))
    session.execute(text('INSERT INTO seen_fund_deposits VALUES (:registration,:run)'),
                    [dict(registration=a,run=b) for a,b in seen])
    session.execute(text('''UPDATE fund_deposits f SET last_seen_at=:seen, source_present=true
        FROM seen_fund_deposits s WHERE f.registration=s.registration AND f.run=s.run'''),dict(seen=fetched_at))
    session.execute(text('''UPDATE fund_deposits f SET source_present=false WHERE NOT EXISTS
        (SELECT 1 FROM seen_fund_deposits s WHERE f.registration=s.registration AND f.run=s.run)'''))
    session.execute(text('DROP TABLE seen_fund_deposits'))
    session.execute(text('''INSERT INTO fund_deposit_imports
        (fetched_at,row_count,inserted,updated,source_url,source_sha256)
        VALUES (:seen,:count,:inserted,:updated,:url,:sha)'''),
        dict(seen=fetched_at,count=len(records),inserted=inserted,updated=updated,url=SOURCE_URL,sha=source_sha256))
    return DownloadResult(downloaded=1,rows_upserted=inserted+updated)


class FundDepositsDownloader(BaseDownloader):
    def __init__(self):
        super().__init__(DOWNLOADS_DIR / 'fund-deposits')

    def run(self):
        with make_session() as session:
            response=session.get(SOURCE_URL,timeout=(15,120))
            response.raise_for_status()
            return self.import_bytes(response.content)

    def import_bytes(self, content):
        records=parse_html(content.decode('utf-8-sig'))
        return load_snapshot(records,fetched_at=datetime.now(timezone.utc),source_sha256=hashlib.sha256(content).hexdigest())

    def backfill(self, from_date=None):
        # This page exposes the full registry in one response, including historical deposits.
        return self.run()


def main():
    parser=argparse.ArgumentParser(description='Import the full CMF fund-deposit registry into the local database')
    parser.add_argument('--file',type=Path,help='Import a previously fetched complete CMF response')
    args=parser.parse_args()
    from src.db.engine import engine
    if engine.url.host not in ('localhost','127.0.0.1','::1'):
        parser.error('Manual command is local-only; production uses the scheduled job.')
    from src.scheduler import _run_tracked
    logging.basicConfig(level=logging.INFO)
    downloader=FundDepositsDownloader()
    print(_run_tracked('fund_deposits',lambda:downloader.import_bytes(args.file.read_bytes()) if args.file else downloader.run()))


if __name__=='__main__':main()
