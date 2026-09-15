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
    week_clp: Decimal
    week_usd: Decimal
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
        FILTER (WHERE cd.fecha >= rd.fecha - INTERVAL '6 days' AND cd.moneda = '$$'), 0) AS week_clp,
    COALESCE(SUM(cd.monto_aportado - cd.monto_rescatado)
        FILTER (WHERE cd.fecha >= rd.fecha - INTERVAL '6 days' AND cd.moneda = 'PROM'), 0) AS week_usd,
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
                week_clp=Decimal(row["week_clp"]),
                week_usd=Decimal(row["week_usd"]),
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
            "daily_clp", "daily_usd", "week_clp", "week_usd",
            "mtd_clp", "mtd_usd", "ytd_clp", "ytd_usd"
        )
    }


def _plain_pair(totals: dict[str, Decimal], period: str) -> str:
    return (
        f"{_format_amount(totals[f'{period}_clp'], 'CLP')} / "
        f"{_format_amount(totals[f'{period}_usd'], 'USD')}"
    )


def _active_types(snapshot: ReportSnapshot) -> tuple[str, ...]:
    present = {row.tipo for row in snapshot.rows}
    return tuple(tipo for tipo in REPORT_TYPES if tipo in present)


def _plain_report(snapshot: ReportSnapshot) -> str:
    lines = [
        "Fondos Mutuos — Net New Money",
        snapshot.report_date.isoformat(),
        "",
        "CLASIFICACIÓN GENERAL",
        "Clasificación | Fondos | Día | 1W | MTD | YTD",
    ]
    for tipo in _active_types(snapshot):
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        totals = _totals(rows)
        lines.append(
            f"{tipo} | {sum(row.funds for row in rows)} | "
            f"{_plain_pair(totals, 'daily')} | {_plain_pair(totals, 'week')} | "
            f"{_plain_pair(totals, 'mtd')} | "
            f"{_plain_pair(totals, 'ytd')}"
        )
    lines.extend(["", "DETALLE", "Clasificación | Fondos | Día | 1W | MTD | YTD"])
    for tipo in _active_types(snapshot):
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        for row in rows:
            totals = {
                field: getattr(row, field)
                for field in (
                    "daily_clp", "daily_usd", "week_clp", "week_usd",
                    "mtd_clp", "mtd_usd", "ytd_clp", "ytd_usd"
                )
            }
            lines.append(
                f"{tipo} / {row.nombre} | {row.funds} | {_plain_pair(totals, 'daily')} | "
                f"{_plain_pair(totals, 'week')} | {_plain_pair(totals, 'mtd')} | "
                f"{_plain_pair(totals, 'ytd')}"
            )
    lines.extend([
        "",
        "NNM = aportes − rescates. CLP y USD por separado; sin conversión FX.",
    ])
    return "\n".join(lines)


def _money_cell(clp: Decimal, usd: Decimal) -> str:
    def line(value: Decimal, currency: str) -> str:
        color = "#15803d" if value > 0 else "#b91c1c"
        return (
            f'<div class="amount" style="color:{color}">'
            f"{html.escape(_format_amount(value, currency))}</div>"
        )
    values = []
    if clp:
        values.append(line(clp, "CLP"))
    if usd:
        values.append(line(usd, "USD"))
    return f'<td class="money">{"".join(values) if values else "—"}</td>'


def _summary_rows(snapshot: ReportSnapshot) -> str:
    rendered = []
    for tipo in _active_types(snapshot):
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        totals = _totals(rows)
        rendered.append(
            "<tr>"
            f'<td class="label"><span class="type">{html.escape(tipo)}</span></td>'
            f'<td class="funds">{sum(row.funds for row in rows)}</td>'
            f"{_money_cell(totals['daily_clp'], totals['daily_usd'])}"
            f"{_money_cell(totals['week_clp'], totals['week_usd'])}"
            f"{_money_cell(totals['mtd_clp'], totals['mtd_usd'])}"
            f"{_money_cell(totals['ytd_clp'], totals['ytd_usd'])}"
            "</tr>"
        )
    return "".join(rendered)


def _detail_rows(snapshot: ReportSnapshot) -> str:
    rendered = []
    for tipo in _active_types(snapshot):
        rows = [row for row in snapshot.rows if row.tipo == tipo]
        rendered.extend(
            "<tr>"
                f'<td class="label"><span class="type muted">{html.escape(tipo)}</span>'
                f'<div class="category">{html.escape(row.nombre)}</div></td>'
                f'<td class="funds">{row.funds}</td>'
                f"{_money_cell(row.daily_clp, row.daily_usd)}"
                f"{_money_cell(row.week_clp, row.week_usd)}"
                f"{_money_cell(row.mtd_clp, row.mtd_usd)}"
                f"{_money_cell(row.ytd_clp, row.ytd_usd)}"
                "</tr>"
            for row in rows
        )
    return "".join(rendered)


def _html_report(snapshot: ReportSnapshot) -> str:
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;padding:32px 12px;background:#f8fafc;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#0f172a}}
.wrap{{max-width:880px;margin:0 auto}}
.top{{border-top:3px solid #001e62;padding:20px 2px 18px}}
.brand{{font-size:12px;font-weight:700;letter-spacing:.08em;color:#001e62;text-transform:uppercase}}
.date{{float:right;color:#64748b;font-size:12px;font-weight:500}}
h1{{font-size:24px;line-height:1.25;margin:13px 0 0;letter-spacing:-.02em}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:20px}}
.card-title{{font-size:14px;font-weight:600;color:#001e62;padding:15px 18px;border-bottom:1px solid #e2e8f0}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th{{background:#f3f6fb;color:#38537a;text-align:right;padding:9px 12px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #dce5f2}}
th:first-child{{text-align:left}}
td{{border-bottom:1px solid #f1f5f9;padding:11px 12px;vertical-align:middle}}
tr:last-child td{{border-bottom:0}}
.label{{text-align:left}} .funds{{text-align:right;color:#475569;font-variant-numeric:tabular-nums}}
.money{{text-align:right;color:#94a3b8;line-height:1.45;font-variant-numeric:tabular-nums;white-space:nowrap}}
.amount{{white-space:nowrap}}
.type{{display:inline-block;font-size:11px;font-weight:600;color:#0f172a}}
.type.muted{{font-size:9px;color:#195ab4;text-transform:uppercase;letter-spacing:.04em}}
.category{{font-size:12px;color:#0f172a;margin-top:3px}}
.note{{color:#64748b;font-size:10px;line-height:1.5;padding:1px 2px}}
@media(max-width:640px){{body{{padding:12px 4px}}h1{{font-size:20px}}th,td{{padding:8px 5px}}table{{font-size:10px}}.category{{font-size:10px}}}}
</style></head>
<body><div class="wrap"><div class="top">
<span class="brand">BTG Pactual</span><span class="date">Datos al {snapshot.report_date.isoformat()}</span>
<h1>Fondos Mutuos · Net New Money</h1></div>
<div class="card"><div class="card-title">Clasificación general</div><table role="presentation">
<thead><tr><th>Clasificación</th><th>Fondos</th><th>Día</th><th>1W</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{_summary_rows(snapshot)}</tbody></table></div>
<div class="card"><div class="card-title">Detalle por clasificación</div><table role="presentation">
<thead><tr><th>Clasificación</th><th>Fondos</th><th>Día</th><th>1W</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{_detail_rows(snapshot)}</tbody></table></div>
<div class="note">NNM = aportes − rescates · CLP y USD por separado · Sin conversión FX · Fuente: CMF</div>
</div></body></html>"""


def send_report(
    snapshot: ReportSnapshot,
    settings: ReportSettings,
) -> str:
    session = make_session(headers={
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"mf-nnm-summary-v4-{snapshot.report_date.isoformat()}",
    })
    try:
        response = session.post(
            RESEND_URL,
            json={
                "from": settings.sender,
                "to": list(settings.recipients),
                "subject": f"BTG | Fondos Mutuos NNM | {snapshot.report_date.isoformat()}",
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
