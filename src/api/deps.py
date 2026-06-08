from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Response


def _pagination(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> tuple[int, int]:
    return limit, offset


def _cache_1h(response: Response) -> None:
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=3600"


Pagination = Annotated[tuple[int, int], Depends(_pagination)]
CacheHook = Annotated[None, Depends(_cache_1h)]
