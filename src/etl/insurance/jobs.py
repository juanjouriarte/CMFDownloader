"""Life-insurer monthly import. Initial local loading requires explicit ZIPs."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.base import BaseDownloader, DownloadResult
from src.config import DOWNLOADS_DIR
from .download import InsuranceArchiveClient, _validate_period
from .loader import load_archive

START_PERIOD = "202608"


def refresh_periods(latest: str) -> list[str]:
    _validate_period(latest)
    year, month = int(latest[:4]), int(latest[4:])
    previous = f"{year if month > 1 else year - 1:04d}{month - 1 if month > 1 else 12:02d}"
    return [p for p in (previous, latest) if p >= START_PERIOD]


class InsurancePortfoliosDownloader(BaseDownloader):
    def __init__(self):
        super().__init__(DOWNLOADS_DIR / "insurance")

    def run(self):
        result = DownloadResult()
        client = InsuranceArchiveClient("CSVID")
        try:
            for period in refresh_periods(client.latest_period()):
                path = client.download(period, self.output_dir)
                imported = load_archive(path)
                # Raw bytes now live in insurance_archives, including revisions.
                path.unlink(missing_ok=True)
                path.with_suffix(".json").unlink(missing_ok=True)
                result += imported
        finally:
            client.close()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", type=Path, help="Import an explicit staged monthly ZIP; repeat for multiple months")
    parser.add_argument("--refresh", action="store_true", help="Download latest two published months, starting August 2026")
    args = parser.parse_args()
    if bool(args.archive) == args.refresh:
        parser.error("Choose explicit --archive files or --refresh")
    from src.db.engine import engine
    from src.scheduler import _run_tracked
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Manual command is local-only; production uses the scheduled worker.")
    logging.basicConfig(level=logging.INFO)
    if args.refresh:
        print(_run_tracked("insurance_portfolios", InsurancePortfoliosDownloader().run), flush=True)
    else:
        for path in args.archive:
            print(f"Importing {path.name}", flush=True)
            print(_run_tracked("insurance_portfolios", lambda: load_archive(path)), flush=True)


if __name__ == "__main__":
    main()
