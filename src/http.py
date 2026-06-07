from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Transport-level retry: automatic on connection errors + 5xx / 429
_RETRY_TOTAL    = 3
_BACKOFF_FACTOR = 0.5   # sleeps: 0 s, 1 s, 2 s between attempts
_STATUS_FORCELIST = {429, 500, 502, 503, 504}


def make_session(headers: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
    })
    if headers:
        session.headers.update(headers)

    retry = Retry(
        total=_RETRY_TOTAL,
        backoff_factor=_BACKOFF_FACTOR,
        status_forcelist=_STATUS_FORCELIST,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch(
    session: requests.Session,
    url: str,
    *,
    method: str = "GET",
    attempts: int = _RETRY_TOTAL,
    backoff: float = _BACKOFF_FACTOR,
    **kwargs,
) -> requests.Response:
    """
    Application-level retry on top of the transport adapter.
    Use when you need to inspect the response before deciding to retry
    (e.g. CMF returning an HTML error page instead of a CSV/XML).
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_exc = exc
            if i < attempts:
                time.sleep(backoff * (2 ** (i - 1)))
    raise last_exc  # type: ignore[misc]
