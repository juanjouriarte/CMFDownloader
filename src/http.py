from __future__ import annotations

import requests


def make_session(headers: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "CMFDownloader/1.0",
    })
    if headers:
        session.headers.update(headers)
    return session
