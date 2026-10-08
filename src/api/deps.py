from __future__ import annotations

from typing import Annotated
import re

from fastapi import Depends, Query, Response, Request


def _pagination(
    limit: int = Query(50, ge=1, le=1500),
    offset: int = Query(0, ge=0),
) -> tuple[int, int]:
    return limit, offset


def _cache_1h(response: Response, request: Request) -> None:
    classification_paths=("/industry", "/categories", "/rentability")
    uses_classification=request.url.path.startswith(classification_paths) or re.fullmatch(r"/(mutual-funds|investment-funds)(/[^/]+)?/?",request.url.path) is not None
    response.headers["Cache-Control"] = ("no-cache" if uses_classification else "public, max-age=3600, s-maxage=3600")


Pagination = Annotated[tuple[int, int], Depends(_pagination)]
CacheHook = Annotated[None, Depends(_cache_1h)]
