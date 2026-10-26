"""Download monthly insurance archives through the CMF's normal CAPTCHA flow.

Stages original ZIPs with provenance for the inspection pilot or persistent job.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from src import captcha
from src.http import make_session

BASE = "https://www.cmfchile.cl"
PAGE = BASE + "/institucional/estadisticas/merc_seguros/cartera_inversiones/dcisgv/descarga_cartera_inv.php"
MAX_ARCHIVE_BYTES = 150_000_000
MAX_EXPANDED_BYTES = 1_500_000_000


def validate_archive(data: bytes) -> list[str]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds download size limit")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("CMF response is not a ZIP archive")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if not entries or len(entries) > 20_000:
            raise ValueError("Unexpected archive member count")
        if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("Expanded archive exceeds size limit")
        if any(entry.flag_bits & 1 for entry in entries):
            raise ValueError("Encrypted archive member")
        if len({entry.filename.upper() for entry in entries}) != len(entries):
            raise ValueError("Duplicate archive member")
        if archive.testzip() is not None:
            raise ValueError("Archive CRC validation failed")
        return [entry.filename for entry in entries]


class InsuranceArchiveClient:
    def __init__(self, group: str = "CSVID"):
        if group not in {"CSVID", "CSGEN"}:
            raise ValueError("group must be CSVID or CSGEN")
        self.group = group
        self.session = make_session(headers={"Referer": PAGE + "?tipoentidad=" + group})

    def close(self) -> None:
        self.session.close()

    def latest_period(self) -> str:
        response = self.session.get(
            PAGE, params={"tipoentidad": self.group, "fnAjax": "per_u"}, timeout=(15, 60)
        )
        response.raise_for_status()
        payload = response.json()
        period = str(payload[0]["u_agno"]) + str(payload[0]["u_vmes"]).zfill(2)
        _validate_period(period)
        return period

    def download(self, period: str, output_dir: Path) -> Path:
        _validate_period(period)
        response = self.session.get(PAGE, params={"tipoentidad": self.group}, timeout=(15, 60))
        response.raise_for_status()
        for _ in range(5):
            image = self.session.get(
                BASE + "/sitio/biblioteca/captcha2/captcha.php",
                params={"rand": secrets.randbelow(32768)}, timeout=(15, 30),
            )
            image.raise_for_status()
            answer = captcha.solve(image.content)
            if not answer:
                continue
            response = self.session.post(
                BASE + "/biblioteca/captcha2/captcha.php",
                data={"accion": "valida", "valor": answer}, timeout=(15, 30),
            )
            response.raise_for_status()
            if response.text.strip() != "1":
                continue
            params = {"tipoentidad": self.group, "fnAjax": "archi", "peri": period}
            response = self.session.get(PAGE, params=params, timeout=(15, 60))
            response.raise_for_status()
            if str(response.json()) != "1":
                raise FileNotFoundError(f"CMF has no archive for {self.group} {period}")
            params["fnAjax"] = "descarga"
            with self.session.get(PAGE, params=params, timeout=(15, 180), stream=True) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(1_048_576):
                    data.extend(chunk)
                    if len(data) > MAX_ARCHIVE_BYTES:
                        raise ValueError("Archive exceeds download size limit")
            members = validate_archive(bytes(data))
            output_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(data).hexdigest()
            dest = output_dir / f"{self.group}-{period}-{digest[:12]}.zip"
            temporary = dest.with_suffix(".zip.part")
            temporary.write_bytes(data)
            temporary.replace(dest)
            dest.with_suffix(".json").write_text(json.dumps({
                "group": self.group, "period": period, "sha256": digest,
                "source_url": PAGE + f"?tipoentidad={self.group}&fnAjax=descarga&peri={period}",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(data), "members": members,
            }, indent=2) + "\n")
            return dest
        raise RuntimeError(f"CAPTCHA validation failed for {self.group} {period}")


def _validate_period(period: str) -> None:
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period):
        raise ValueError("period must be YYYYMM")
