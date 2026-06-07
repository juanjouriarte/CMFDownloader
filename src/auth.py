from __future__ import annotations

import os

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """Validate bearer token from API_TOKEN env var.
    If API_TOKEN is not set the check is skipped (local dev mode).
    """
    expected = os.getenv("API_TOKEN", "").strip()
    if not expected:
        return
    if not credentials or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
