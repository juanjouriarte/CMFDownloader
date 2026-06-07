from __future__ import annotations

import logging
import re
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from src.auth import require_token
from src.financialStatements.downloaders.financialStatementsDownloader import (
    FinancialStatementsDownloader,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/financial-statements", tags=["financial-statements"])

_PERIOD_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])$")


def _validate_period(value: str, field: str) -> None:
    if not _PERIOD_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be in YYYYMM format with a valid month (e.g. 202603), got '{value}'",
        )


def _run_download(inicio: str, termino: str) -> dict:
    logger.info("Financial statements download: %s → %s", inicio, termino)
    result = FinancialStatementsDownloader(force=True).download_period(inicio, termino)
    return asdict(result)


@router.post("/download", dependencies=[Depends(require_token)])
def download_financial_statements(
    background_tasks: BackgroundTasks,
    inicio: str = Query(..., description="Start period YYYYMM, e.g. 202003"),
    termino: str = Query(..., description="End period YYYYMM, e.g. 202012"),
    background: bool = Query(False, description="Run asynchronously and return immediately"),
):
    """
    Trigger a financial-statements download for the given period range.

    CMF returns all quarterly statements between `inicio` and `termino` in a
    single file. Full-year ranges (e.g. 202003→202012) work for complete years;
    for the current/most-recent year query one quarter at a time.
    """
    _validate_period(inicio, "inicio")
    _validate_period(termino, "termino")
    if inicio > termino:
        raise HTTPException(status_code=422, detail="inicio must be <= termino")

    if background:
        background_tasks.add_task(_run_download, inicio, termino)
        return {"status": "accepted", "inicio": inicio, "termino": termino,
                "message": "Download started in background"}

    result = _run_download(inicio, termino)
    return {"status": "completed", "inicio": inicio, "termino": termino, **result}
