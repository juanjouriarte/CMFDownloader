from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]
DOWNLOADS_DIR: Path = Path(os.getenv("DOWNLOADS_DIR", "./downloads"))


BASE = "https://www.cmfchile.cl"


class CMFUrl(StrEnum):
    FM_IDENTIDAD = f"{BASE}/institucional/estadisticas/fm_ident2.php"
