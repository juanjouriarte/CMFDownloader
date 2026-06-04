from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadResult:
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    rows_upserted: int = 0

    def __add__(self, other: DownloadResult) -> DownloadResult:
        return DownloadResult(
            downloaded=self.downloaded + other.downloaded,
            skipped=self.skipped + other.skipped,
            errors=self.errors + other.errors,
            rows_upserted=self.rows_upserted + other.rows_upserted,
        )


class BaseDownloader:
    def __init__(self, output_dir: Path, force: bool = False) -> None:
        self.output_dir = output_dir
        self.force = force
        self.logger = logging.getLogger(self.__class__.__name__)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _should_skip(self, path: Path) -> bool:
        return path.exists() and not self.force

    def run(self) -> DownloadResult:
        raise NotImplementedError
