from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.environ["DATABASE_URL"]
DOWNLOADS_DIR: Path = Path(os.getenv("DOWNLOADS_DIR", "./downloads"))


GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

BASE = "https://www.cmfchile.cl"


class CMFUrl(StrEnum):
    FM_IDENTIDAD   = f"{BASE}/institucional/estadisticas/fm_ident2.php"
    CARTOLA_PAGE   = f"{BASE}/institucional/estadisticas/fondos_cartola_diaria.php?tpl=alt"
    CARTOLA_POST   = f"{BASE}/institucional/estadisticas/cfm_download.php"
    CARTOLA_CAPTCHA = f"{BASE}/sitio/biblioteca/captcha2/captcha.php"
