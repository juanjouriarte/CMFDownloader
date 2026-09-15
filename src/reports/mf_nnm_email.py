from __future__ import annotations

import html
import logging
import os
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
    daily_clp: Decimal
    daily_usd: Decimal
    mtd_clp: Decimal
    mtd_usd: Decimal
    ytd_clp: Decimal
    ytd_usd: Decimal
    unsupported_currency_rows: int


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
        FILTER (WHERE cd.fecha = rd.fecha AND cd.moneda = '$$'), 0) AS daily_clp,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha = rd.fecha AND cd.moneda = 'PROM'), 0) AS daily_usd,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha >= DATE_TRUNC('month', rd.fecha) AND cd.moneda = '$$'), 0) AS mtd_clp,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha >= DATE_TRUNC('month', rd.fecha) AND cd.moneda = 'PROM'), 0) AS mtd_usd,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.moneda = '$$'), 0) AS ytd_clp,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.moneda = 'PROM'), 0) AS ytd_usd,
    COUNT(*) FILTER (
        WHERE cd.moneda IS NULL OR cd.moneda NOT IN ('$$', 'PROM')
    ) AS unsupported_currency_rows
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

    snapshot = ReportSnapshot(
        report_date=report_date,
        rows=tuple(
            CategoryFlow(
                tipo=row["tipo"],
                categoria=row["categoria"],
                nombre=row["nombre_cat"],
                funds=int(row["funds"]),
                daily_clp=Decimal(row["daily_clp"]),
                daily_usd=Decimal(row["daily_usd"]),
                mtd_clp=Decimal(row["mtd_clp"]),
                mtd_usd=Decimal(row["mtd_usd"]),
                ytd_clp=Decimal(row["ytd_clp"]),
                ytd_usd=Decimal(row["ytd_usd"]),
                unsupported_currency_rows=int(row["unsupported_currency_rows"]),
            )
            for row in rows
        ),
    )
    unsupported = sum(row.unsupported_currency_rows for row in snapshot.rows)
    if unsupported:
        raise RuntimeError(
            f"Cannot build currency-safe report: {unsupported} flow rows use an unsupported currency"
        )
    return snapshot


def _format_amount(value: Decimal, currency: str) -> str:
    if currency == "CLP":
        return f"{value / Decimal('1000000000'):,.2f} bn CLP"
    return f"{value / Decimal('1000000'):,.2f} mm USD"


def _totals(rows: list[CategoryFlow]) -> dict[str, Decimal]:
    return {
        field: sum((getattr(row, field) for row in rows), Decimal(0))
        for field in (
            "daily_clp", "daily_usd", "mtd_clp", "mtd_usd", "ytd_clp", "ytd_usd"
        )
    }


def _plain_pair(totals: dict[str, Decimal], period: str) -> str:
    return (
        f"{_format_amount(totals[f'{period}_clp'], 'CLP')} / "
        f"{_format_amount(totals[f'{period}_usd'], 'USD')}"
    )


def _plain_report(snapshot: ReportSnapshot) -> str:
    lines = [
        "BTG Pactual — CMF Mutual Funds — Net New Money",
        f"Data through {snapshot.report_date.isoformat()}",
        "",
        "HIGH-LEVEL SUMMARY",
        "Classification | Funds | Daily | MTD | YTD",
    ]
    for tipo in REPORT_TYPES:
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        totals = _totals(rows)
        lines.append(
            f"{tipo} | {sum(row.funds for row in rows)} | "
            f"{_plain_pair(totals, 'daily')} | {_plain_pair(totals, 'mtd')} | "
            f"{_plain_pair(totals, 'ytd')}"
        )
    lines.extend(["", "DETAILED CLASSIFICATIONS"])
    for tipo in REPORT_TYPES:
        lines.extend(["", tipo, "Classification | Funds | Daily | MTD | YTD"])
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        if not rows:
            lines.append("No funds are currently assigned to this classification.")
        for row in rows:
            totals = {
                field: getattr(row, field)
                for field in (
                    "daily_clp", "daily_usd", "mtd_clp", "mtd_usd", "ytd_clp", "ytd_usd"
                )
            }
            lines.append(
                f"{row.nombre} | {row.funds} | {_plain_pair(totals, 'daily')} | "
                f"{_plain_pair(totals, 'mtd')} | {_plain_pair(totals, 'ytd')}"
            )
    lines.extend([
        "",
        "CLP and USD are reported separately; no FX conversion is applied.",
        "Net new money = contributions − redemptions.",
    ])
    return "\n".join(lines)


def _money_cell(clp: Decimal, usd: Decimal) -> str:
    def line(value: Decimal, currency: str) -> str:
        color = "#137333" if value >= 0 else "#c5221f"
        return (
            f'<div style="color:{color};white-space:nowrap">'
            f"{html.escape(_format_amount(value, currency))}</div>"
        )
    return f'<td class="money">{line(clp, "CLP")}{line(usd, "USD")}</td>'


def _summary_rows(snapshot: ReportSnapshot) -> str:
    rendered = []
    for tipo in REPORT_TYPES:
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        totals = _totals(rows)
        rendered.append(
            "<tr>"
            f'<td class="label"><strong>{html.escape(tipo)}</strong></td>'
            f'<td class="funds">{sum(row.funds for row in rows)}</td>'
            f"{_money_cell(totals['daily_clp'], totals['daily_usd'])}"
            f"{_money_cell(totals['mtd_clp'], totals['mtd_usd'])}"
            f"{_money_cell(totals['ytd_clp'], totals['ytd_usd'])}"
            "</tr>"
        )
    return "".join(rendered)


def _detail_sections(snapshot: ReportSnapshot) -> str:
    sections = []
    for tipo in REPORT_TYPES:
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        if rows:
            body = "".join(
                "<tr>"
                f'<td class="label">{html.escape(row.nombre)}</td>'
                f'<td class="funds">{row.funds}</td>'
                f"{_money_cell(row.daily_clp, row.daily_usd)}"
                f"{_money_cell(row.mtd_clp, row.mtd_usd)}"
                f"{_money_cell(row.ytd_clp, row.ytd_usd)}"
                "</tr>"
                for row in rows
            )
        else:
            body = (
                '<tr><td class="empty" colspan="5">'
                "No funds are currently assigned to this classification.</td></tr>"
            )
        sections.append(f"""
<div class="section-title">{html.escape(tipo)}</div>
<table role="presentation">
<thead><tr><th>Detailed classification</th><th>Funds</th><th>Daily</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{body}</tbody>
</table>""")
    return "".join(sections)


def _html_report(snapshot: ReportSnapshot) -> str:
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;background:#f3f6fa;font-family:Arial,Helvetica,sans-serif;color:#1f2a44}}
.wrap{{max-width:920px;margin:0 auto;background:#fff}}
.header{{background:#001e62;color:#fff;padding:30px 36px;border-bottom:6px solid #418fde}}
.brand{{font-size:24px;font-weight:800;letter-spacing:.5px}}
.eyebrow{{font-size:11px;letter-spacing:1.7px;color:#b8ccea;margin-top:7px}}
.content{{padding:30px 36px}}
.date{{display:inline-block;background:#eaf1fb;color:#001e62;padding:7px 11px;border-radius:3px;font-size:12px}}
h1{{font-size:25px;color:#001e62;margin:18px 0 5px}}
.subtitle{{color:#5b6780;margin:0 0 24px}}
.section-title{{font-size:17px;font-weight:700;color:#001e62;margin:30px 0 8px;border-left:5px solid #195ab4;padding-left:10px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:15px}}
th{{background:#001e62;color:#fff;text-align:right;padding:10px 8px;font-size:11px;text-transform:uppercase;letter-spacing:.3px}}
th:first-child{{text-align:left}}
td{{border-bottom:1px solid #dce4ef;padding:10px 8px;vertical-align:top}}
.label{{text-align:left}} .funds{{text-align:right}} .money{{text-align:right;line-height:1.55}}
.empty{{text-align:center;color:#6e7890;font-style:italic}}
.note{{background:#f5f8fc;border-left:4px solid #418fde;padding:13px 15px;color:#526079;font-size:12px;line-height:1.5;margin-top:25px}}
.footer{{background:#001e62;color:#b8ccea;padding:18px 36px;font-size:11px}}
@media(max-width:640px){{.content,.header{{padding-left:16px;padding-right:16px}}table{{font-size:11px}}td,th{{padding:8px 4px}}}}
</style></head>
<body><div class="wrap">
<div class="header"><div class="brand">BTG PACTUAL</div><div class="eyebrow">ASSET MANAGEMENT INTELLIGENCE</div></div>
<div class="content">
<span class="date">DATA THROUGH {snapshot.report_date.isoformat()}</span>
<h1>Mutual Funds — Net New Money</h1>
<p class="subtitle">Daily market flows with month-to-date and year-to-date context.</p>
<div class="section-title">High-level overview</div>
<table role="presentation">
<thead><tr><th>Classification</th><th>Funds</th><th>Daily</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{_summary_rows(snapshot)}</tbody>
</table>
<div class="section-title">Detailed classifications</div>
{_detail_sections(snapshot)}
<div class="note"><strong>Currency methodology:</strong> CLP and USD flows are shown separately. No FX conversion is applied. CMF code <strong>$$</strong> is reported as CLP and <strong>PROM</strong> as USD.<br><strong>Net new money</strong> = contributions − redemptions.</div>
</div>
<div class="footer">Source: Comisión para el Mercado Financiero (CMF) · Automated market report</div>
</div></body></html>"""


def send_report(
    snapshot: ReportSnapshot,
    settings: ReportSettings,
) -> str:
    session = make_session(headers={
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"mf-nnm-summary-v2-{snapshot.report_date.isoformat()}",
    })
    try:
        response = session.post(
            RESEND_URL,
            json={
                "from": settings.sender,
                "to": list(settings.recipients),
                "subject": f"BTG | Mutual Funds NNM | {snapshot.report_date.isoformat()}",
                "text": _plain_report(snapshot),
                "html": _html_report(snapshot),
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
        return DownloadResult(skipped=1)

    snapshot = load_snapshot()
    message_id = send_report(snapshot, settings)
    logger.info("Sent consolidated mutual-fund NNM report id=%s", message_id)
    return DownloadResult(downloaded=1)
