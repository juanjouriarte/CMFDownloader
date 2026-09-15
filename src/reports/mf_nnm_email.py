from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from src.base import DownloadResult
from src.categories import TipoFondo
from src.db.engine import SessionLocal
from src.http import make_session

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
REPORT_TYPES = tuple(tipo.value for tipo in TipoFondo)


@dataclass(frozen=True)
class ReportSettings:
    enabled: bool
    api_key: str
    sender: str
    recipients: tuple[str, ...]

    @classmethod
    def from_env(cls) -> ReportSettings:
        enabled = os.getenv("NNM_EMAIL_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        recipients = tuple(
            address.strip()
            for address in os.getenv("NNM_EMAIL_RECIPIENTS", "").split(",")
            if address.strip()
        )
        settings = cls(
            enabled=enabled,
            api_key=os.getenv("RESEND_API_KEY", "").strip(),
            sender=os.getenv(
                "NNM_EMAIL_FROM", "CMF Reports <onboarding@resend.dev>"
            ).strip(),
            recipients=recipients,
        )
        if enabled:
            missing = []
            if not settings.api_key:
                missing.append("RESEND_API_KEY")
            if not settings.sender:
                missing.append("NNM_EMAIL_FROM")
            if not settings.recipients:
                missing.append("NNM_EMAIL_RECIPIENTS")
            if missing:
                raise ValueError(
                    "Email reporting is enabled but configuration is missing: "
                    + ", ".join(missing)
                )
        return settings


@dataclass(frozen=True)
class CategoryFlow:
    tipo: str
    categoria: str
    nombre: str
    funds: int
    daily: Decimal
    mtd: Decimal
    ytd: Decimal


@dataclass(frozen=True)
class ReportSnapshot:
    report_date: date
    rows: tuple[CategoryFlow, ...]


_REPORT_SQL = text("""
WITH daily_counts AS (
    SELECT fecha, COUNT(DISTINCT run_fondo) AS fund_count
    FROM cartola_diaria
    WHERE fecha >= (SELECT MAX(fecha) - INTERVAL '6 days' FROM cartola_diaria)
      AND monto_aportado IS NOT NULL
    GROUP BY fecha
),
eligible_dates AS (
    SELECT fecha, fund_count, MAX(fund_count) OVER () AS max_fund_count
    FROM daily_counts
),
reference_date AS (
    SELECT MAX(fecha) AS fecha
    FROM eligible_dates
    WHERE fund_count >= max_fund_count * 0.90
),
latest_categories AS (
    SELECT DISTINCT ON (run_fondo)
           run_fondo, categoria, tipo, nombre_cat
    FROM categoria_fm
    ORDER BY run_fondo, periodo DESC
)
SELECT
    rd.fecha AS report_date,
    lc.tipo,
    lc.categoria,
    lc.nombre_cat,
    COUNT(DISTINCT cd.run_fondo) AS funds,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha = rd.fecha), 0) AS daily,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha >= DATE_TRUNC('month', rd.fecha)), 0) AS mtd,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado), 0) AS ytd
FROM reference_date rd
JOIN cartola_diaria cd
  ON cd.fecha >= DATE_TRUNC('year', rd.fecha)
 AND cd.fecha <= rd.fecha
JOIN latest_categories lc ON lc.run_fondo = cd.run_fondo
WHERE cd.monto_aportado IS NOT NULL
GROUP BY rd.fecha, lc.tipo, lc.categoria, lc.nombre_cat
ORDER BY lc.tipo, lc.nombre_cat
""")


def load_snapshot() -> ReportSnapshot:
    with SessionLocal() as session:
        rows = session.execute(_REPORT_SQL).mappings().all()

    if not rows:
        raise RuntimeError(
            "No mutual-fund classifications are available. Run mf_categories first."
        )

    report_date = rows[0]["report_date"]
    if report_date is None:
        raise RuntimeError("No mutual-fund flow date is available.")

    return ReportSnapshot(
        report_date=report_date,
        rows=tuple(
            CategoryFlow(
                tipo=row["tipo"],
                categoria=row["categoria"],
                nombre=row["nombre_cat"],
                funds=int(row["funds"]),
                daily=Decimal(row["daily"]),
                mtd=Decimal(row["mtd"]),
                ytd=Decimal(row["ytd"]),
            )
            for row in rows
        ),
    )


def _format_clp(value: Decimal) -> str:
    billions = value / Decimal("1000000000")
    return f"{billions:,.2f} bn CLP"


def _plain_report(tipo: str, snapshot: ReportSnapshot) -> str:
    rows = [row for row in snapshot.rows if row.tipo == tipo]
    lines = [
        f"CMF Mutual Funds — Net New Money — {tipo}",
        f"Data through {snapshot.report_date.isoformat()}",
        "",
        "Classification | Funds | Daily | MTD | YTD",
    ]
    if not rows:
        lines.append("No funds are currently assigned to this classification.")
    for row in rows:
        lines.append(
            f"{row.nombre} | {row.funds} | {_format_clp(row.daily)} | "
            f"{_format_clp(row.mtd)} | {_format_clp(row.ytd)}"
        )
    if rows:
        lines.extend([
            "",
            "Total | "
            + str(sum(row.funds for row in rows))
            + " | "
            + " | ".join(
                _format_clp(sum((getattr(row, period) for row in rows), Decimal(0)))
                for period in ("daily", "mtd", "ytd")
            ),
        ])
    return "\n".join(lines)


def _html_report(tipo: str, snapshot: ReportSnapshot) -> str:
    rows = [row for row in snapshot.rows if row.tipo == tipo]

    def cell(value: Decimal) -> str:
        color = "#137333" if value >= 0 else "#c5221f"
        return f'<td style="text-align:right;color:{color}">{_format_clp(value)}</td>'

    body_rows = []
    for row in rows:
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row.nombre)}</td>"
            f'<td style="text-align:right">{row.funds}</td>'
            f"{cell(row.daily)}{cell(row.mtd)}{cell(row.ytd)}"
            "</tr>"
        )

    if rows:
        totals = {
            period: sum((getattr(row, period) for row in rows), Decimal(0))
            for period in ("daily", "mtd", "ytd")
        }
        body_rows.append(
            '<tr style="font-weight:bold;border-top:2px solid #555">'
            "<td>Total</td>"
            f'<td style="text-align:right">{sum(row.funds for row in rows)}</td>'
            f"{cell(totals['daily'])}{cell(totals['mtd'])}{cell(totals['ytd'])}"
            "</tr>"
        )
    else:
        body_rows.append(
            '<tr><td colspan="5">No funds are currently assigned to this classification.</td></tr>'
        )

    return f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#202124">
<h2>CMF Mutual Funds — Net New Money</h2>
<p><strong>{html.escape(tipo)}</strong><br>
Data through {snapshot.report_date.isoformat()}</p>
<table cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:900px">
<thead><tr style="background:#f1f3f4;text-align:left">
<th>Classification</th><th style="text-align:right">Funds</th>
<th style="text-align:right">Daily</th><th style="text-align:right">MTD</th>
<th style="text-align:right">YTD</th></tr></thead>
<tbody>{''.join(body_rows)}</tbody>
</table>
<p style="color:#5f6368;font-size:12px">Net new money = contributions − redemptions. Source: CMF cartola diaria.</p>
</body></html>"""


def _idempotency_key(tipo: str, report_date: date) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", tipo.lower()).strip("-")
    return f"mf-nnm-{report_date.isoformat()}-{slug}"


def send_report(
    tipo: str,
    snapshot: ReportSnapshot,
    settings: ReportSettings,
) -> str:
    session = make_session(headers={
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": _idempotency_key(tipo, snapshot.report_date),
    })
    try:
        response = session.post(
            RESEND_URL,
            json={
                "from": settings.sender,
                "to": list(settings.recipients),
                "subject": (
                    f"CMF NNM — {tipo} — {snapshot.report_date.isoformat()}"
                ),
                "text": _plain_report(tipo, snapshot),
                "html": _html_report(tipo, snapshot),
            },
            timeout=30,
        )
    finally:
        session.close()
    response.raise_for_status()
    message_id = response.json().get("id")
    if not message_id:
        raise RuntimeError("Resend accepted the request without returning a message ID")
    return str(message_id)


def run() -> DownloadResult:
    settings = ReportSettings.from_env()
    if not settings.enabled:
        logger.info("Mutual-fund NNM emails are disabled")
        return DownloadResult(skipped=len(REPORT_TYPES))

    snapshot = load_snapshot()
    sent = 0
    for tipo in REPORT_TYPES:
        message_id = send_report(tipo, snapshot, settings)
        sent += 1
        logger.info("Sent mutual-fund NNM report tipo=%s id=%s", tipo, message_id)
    return DownloadResult(downloaded=sent)
