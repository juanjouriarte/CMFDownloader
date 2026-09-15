from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.base import DownloadResult
from src.categories import TipoFondo
from src.db.engine import SessionLocal
from src.http import make_session

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
REPORT_TYPES = tuple(tipo.value for tipo in TipoFondo)
FI_REPORT_TYPES = (
    "Alternativo", "Accionario", "Deuda", "Fondo de Fondos", "Balanceado", "Otro",
)
CURRENCY_ORDER = ("CLP", "USD", "EUR", "COP", "PEN")
SANTIAGO_TZ = ZoneInfo("America/Santiago")


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
class CurrencyFlow:
    currency: str
    daily: Decimal
    week: Decimal
    mtd: Decimal
    ytd: Decimal


@dataclass(frozen=True)
class CategoryFlow:
    tipo: str
    categoria: str
    nombre: str
    funds: int
    currencies: tuple[CurrencyFlow, ...]
    unsupported_currency_rows: int


@dataclass(frozen=True)
class ReportSnapshot:
    report_date: date
    rows: tuple[CategoryFlow, ...]
    fi_report_date: date | None = None
    fi_rows: tuple[CategoryFlow, ...] = ()


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


_FI_REPORT_SQL = text("""
WITH daily_counts AS (
    SELECT v.fecha, COUNT(DISTINCT v.run_fondo) AS fund_count
    FROM valores_cuota_fi v
    JOIN fondos_inversion fi ON fi.run_fondo = v.run_fondo
    WHERE fi.rescatable IS NOT NULL
      AND v.flujo_neto IS NOT NULL
      AND v.fecha >= (
          SELECT MAX(v2.fecha) - INTERVAL '6 days'
          FROM valores_cuota_fi v2
          JOIN fondos_inversion fi2 ON fi2.run_fondo = v2.run_fondo
          WHERE fi2.rescatable IS NOT NULL AND v2.flujo_neto IS NOT NULL
      )
    GROUP BY v.fecha
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
    FROM categoria_fi
    ORDER BY run_fondo, periodo DESC
),
flows AS (
    SELECT
        v.*,
        CASE
            WHEN v.moneda IS NULL OR v.moneda = '0' THEN fi.moneda
            ELSE v.moneda
        END AS effective_currency
    FROM valores_cuota_fi v
    JOIN fondos_inversion fi ON fi.run_fondo = v.run_fondo
    WHERE fi.rescatable IS NOT NULL AND v.flujo_neto IS NOT NULL
)
SELECT
    rd.fecha AS report_date,
    COALESCE(lc.tipo, 'Otro') AS tipo,
    COALESCE(lc.categoria, 'FI_OTRO') AS categoria,
    COALESCE(lc.nombre_cat, 'Sin clasificar') AS nombre_cat,
    COUNT(DISTINCT v.run_fondo) AS funds,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha = rd.fecha AND v.effective_currency = '$$'), 0) AS daily_clp,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha = rd.fecha AND v.effective_currency = 'PROM'), 0) AS daily_usd,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha = rd.fecha AND v.effective_currency = 'EUR'), 0) AS daily_eur,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha = rd.fecha AND v.effective_currency = 'COP'), 0) AS daily_cop,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha = rd.fecha AND v.effective_currency = 'PEN'), 0) AS daily_pen,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= rd.fecha - INTERVAL '6 days' AND v.effective_currency = '$$'), 0) AS week_clp,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= rd.fecha - INTERVAL '6 days' AND v.effective_currency = 'PROM'), 0) AS week_usd,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= rd.fecha - INTERVAL '6 days' AND v.effective_currency = 'EUR'), 0) AS week_eur,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= rd.fecha - INTERVAL '6 days' AND v.effective_currency = 'COP'), 0) AS week_cop,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= rd.fecha - INTERVAL '6 days' AND v.effective_currency = 'PEN'), 0) AS week_pen,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= DATE_TRUNC('month', rd.fecha) AND v.effective_currency = '$$'), 0) AS mtd_clp,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= DATE_TRUNC('month', rd.fecha) AND v.effective_currency = 'PROM'), 0) AS mtd_usd,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= DATE_TRUNC('month', rd.fecha) AND v.effective_currency = 'EUR'), 0) AS mtd_eur,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= DATE_TRUNC('month', rd.fecha) AND v.effective_currency = 'COP'), 0) AS mtd_cop,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.fecha >= DATE_TRUNC('month', rd.fecha) AND v.effective_currency = 'PEN'), 0) AS mtd_pen,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.effective_currency = '$$'), 0) AS ytd_clp,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.effective_currency = 'PROM'), 0) AS ytd_usd,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.effective_currency = 'EUR'), 0) AS ytd_eur,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.effective_currency = 'COP'), 0) AS ytd_cop,
    COALESCE(SUM(v.flujo_neto)
        FILTER (WHERE v.effective_currency = 'PEN'), 0) AS ytd_pen,
    COUNT(*) FILTER (
        WHERE v.effective_currency IS NULL
           OR v.effective_currency NOT IN ('$$', 'PROM', 'EUR', 'COP', 'PEN')
    ) AS unsupported_currency_rows
FROM reference_date rd
JOIN flows v
  ON v.fecha >= DATE_TRUNC('year', rd.fecha)
 AND v.fecha <= rd.fecha
LEFT JOIN latest_categories lc ON lc.run_fondo = v.run_fondo
GROUP BY rd.fecha,
         COALESCE(lc.tipo, 'Otro'),
         COALESCE(lc.categoria, 'FI_OTRO'),
         COALESCE(lc.nombre_cat, 'Sin clasificar')
ORDER BY tipo, nombre_cat
""")


def _currency_flow(row, currency: str, suffix: str) -> CurrencyFlow:
    return CurrencyFlow(
        currency=currency,
        daily=Decimal(row[f"daily_{suffix}"]),
        week=Decimal(row[f"week_{suffix}"]),
        mtd=Decimal(row[f"mtd_{suffix}"]),
        ytd=Decimal(row[f"ytd_{suffix}"]),
    )


def _category_flow(row, currencies: tuple[tuple[str, str], ...]) -> CategoryFlow:
    return CategoryFlow(
        tipo=row["tipo"],
        categoria=row["categoria"],
        nombre=row["nombre_cat"],
        funds=int(row["funds"]),
        currencies=tuple(
            _currency_flow(row, currency, suffix)
            for currency, suffix in currencies
        ),
        unsupported_currency_rows=int(row["unsupported_currency_rows"]),
    )


def load_snapshot() -> ReportSnapshot:
    with SessionLocal() as session:
        rows = session.execute(_REPORT_SQL).mappings().all()
        fi_rows = session.execute(_FI_REPORT_SQL).mappings().all()

    if not rows:
        raise RuntimeError(
            "No mutual-fund classifications are available. Run mf_categories first."
        )

    report_date = rows[0]["report_date"]
    if report_date is None:
        raise RuntimeError("No mutual-fund flow date is available.")

    if not fi_rows or fi_rows[0]["report_date"] is None:
        raise RuntimeError(
            "No investment-fund daily flows or classifications are available."
        )

    fi_currencies = (
        ("CLP", "clp"), ("USD", "usd"), ("EUR", "eur"),
        ("COP", "cop"), ("PEN", "pen"),
    )

    snapshot = ReportSnapshot(
        report_date=report_date,
        rows=tuple(
            _category_flow(row, (("CLP", "clp"), ("USD", "usd")))
            for row in rows
        ),
        fi_report_date=fi_rows[0]["report_date"],
        fi_rows=tuple(
            _category_flow(row, fi_currencies)
            for row in fi_rows
        ),
    )
    unsupported = sum(
        row.unsupported_currency_rows
        for row in (*snapshot.rows, *snapshot.fi_rows)
    )
    if unsupported:
        raise RuntimeError(
            f"Cannot build currency-safe report: {unsupported} flow rows use an unsupported currency"
        )
    return snapshot


def _format_amount(value: Decimal, currency: str) -> str:
    if currency == "CLP":
        return f"{value / Decimal('1000000000'):,.2f} bn CLP"
    return f"{value / Decimal('1000000'):,.2f} mm {currency}"


def _amounts(row: CategoryFlow, period: str) -> dict[str, Decimal]:
    return {flow.currency: getattr(flow, period) for flow in row.currencies}


def _totals(rows: list[CategoryFlow], period: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        for currency, value in _amounts(row, period).items():
            totals[currency] = totals.get(currency, Decimal(0)) + value
    return totals


def _ordered_amounts(amounts: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
    return [
        (currency, amounts.get(currency, Decimal(0)))
        for currency in CURRENCY_ORDER
        if amounts.get(currency, Decimal(0))
    ]


def _plain_amounts(amounts: dict[str, Decimal]) -> str:
    values = [
        _format_amount(value, currency)
        for currency, value in _ordered_amounts(amounts)
    ]
    return " / ".join(values) if values else "—"


def _active_types(
    rows: tuple[CategoryFlow, ...],
    preferred_order: tuple[str, ...],
) -> tuple[str, ...]:
    present = {row.tipo for row in rows}
    ordered = [tipo for tipo in preferred_order if tipo in present]
    ordered.extend(sorted(present.difference(ordered)))
    return tuple(ordered)


def _plain_section(
    title: str,
    report_date: date,
    section_rows: tuple[CategoryFlow, ...],
    type_order: tuple[str, ...],
) -> list[str]:
    lines = [
        title,
        f"Datos al {report_date.isoformat()}",
        "",
        "CLASIFICACIÓN GENERAL",
        "Clasificación | Fondos | Día | 1W | MTD | YTD",
    ]
    for tipo in _active_types(section_rows, type_order):
        rows = [row for row in section_rows if row.tipo == tipo]
        lines.append(
            f"{tipo} | {sum(row.funds for row in rows)} | "
            f"{_plain_amounts(_totals(rows, 'daily'))} | "
            f"{_plain_amounts(_totals(rows, 'week'))} | "
            f"{_plain_amounts(_totals(rows, 'mtd'))} | "
            f"{_plain_amounts(_totals(rows, 'ytd'))}"
        )
    lines.extend(["", "DETALLE", "Clasificación | Fondos | Día | 1W | MTD | YTD"])
    for tipo in _active_types(section_rows, type_order):
        for row in (row for row in section_rows if row.tipo == tipo):
            lines.append(
                f"{tipo} / {row.nombre} | {row.funds} | "
                f"{_plain_amounts(_amounts(row, 'daily'))} | "
                f"{_plain_amounts(_amounts(row, 'week'))} | "
                f"{_plain_amounts(_amounts(row, 'mtd'))} | "
                f"{_plain_amounts(_amounts(row, 'ytd'))}"
            )
    return lines


def _plain_report(snapshot: ReportSnapshot) -> str:
    lines = _plain_section(
        "Fondos Mutuos — Net New Money",
        snapshot.report_date,
        snapshot.rows,
        REPORT_TYPES,
    )
    if snapshot.fi_report_date and snapshot.fi_rows:
        lines.extend([""])
        lines.extend(_plain_section(
            "Fondos de Inversión — Flujo Neto",
            snapshot.fi_report_date,
            snapshot.fi_rows,
            FI_REPORT_TYPES,
        ))
    lines.extend([
        "",
        "Fondos Mutuos: NNM = aportes − rescates.",
        "Fondos de Inversión: flujo implícito por variación de cuotas.",
        "En fondos no rescatables, las salidas representan reducciones de cuotas, no rescates contractuales.",
        "Monedas informadas por separado; sin conversión FX.",
    ])
    return "\n".join(lines)


def _money_cell(amounts: dict[str, Decimal]) -> str:
    def line(value: Decimal, currency: str) -> str:
        color = "#15803d" if value > 0 else "#b91c1c"
        return (
            f'<div class="amount" style="color:{color}">'
            f"{html.escape(_format_amount(value, currency))}</div>"
        )
    values = [line(value, currency) for currency, value in _ordered_amounts(amounts)]
    return f'<td class="money">{"".join(values) if values else "—"}</td>'


def _summary_rows(
    section_rows: tuple[CategoryFlow, ...],
    type_order: tuple[str, ...],
) -> str:
    rendered = []
    for tipo in _active_types(section_rows, type_order):
        rows = [row for row in section_rows if row.tipo == tipo]
        rendered.append(
            "<tr>"
            f'<td class="label"><span class="type">{html.escape(tipo)}</span></td>'
            f'<td class="funds">{sum(row.funds for row in rows)}</td>'
            f"{_money_cell(_totals(rows, 'daily'))}"
            f"{_money_cell(_totals(rows, 'week'))}"
            f"{_money_cell(_totals(rows, 'mtd'))}"
            f"{_money_cell(_totals(rows, 'ytd'))}"
            "</tr>"
        )
    return "".join(rendered)


def _detail_sections(
    section_rows: tuple[CategoryFlow, ...],
    type_order: tuple[str, ...],
) -> str:
    sections = []
    for tipo in _active_types(section_rows, type_order):
        rows = [row for row in section_rows if row.tipo == tipo]
        body = "".join(
            "<tr>"
            f'<td class="label"><div class="category">{html.escape(row.nombre)}</div></td>'
            f'<td class="funds">{row.funds}</td>'
            f"{_money_cell(_amounts(row, 'daily'))}"
            f"{_money_cell(_amounts(row, 'week'))}"
            f"{_money_cell(_amounts(row, 'mtd'))}"
            f"{_money_cell(_amounts(row, 'ytd'))}"
            "</tr>"
            for row in rows
        )
        sections.append(f"""
<div class="card detail-card">
<div class="card-title"><span>{html.escape(tipo)}</span><span class="count">{sum(row.funds for row in rows)} fondos</span></div>
<table role="presentation">
<thead><tr><th>Clasificación</th><th>Fondos</th><th>Día</th><th>1W</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{body}</tbody>
</table></div>""")
    return "".join(sections)


def _html_section(
    title: str,
    report_date: date,
    section_rows: tuple[CategoryFlow, ...],
    type_order: tuple[str, ...],
) -> str:
    return f"""
<div class="section-head"><h2>{html.escape(title)}</h2><span>Datos al {report_date.isoformat()}</span></div>
<div class="card"><div class="card-title">Clasificación general</div><table role="presentation">
<thead><tr><th>Clasificación</th><th>Fondos</th><th>Día</th><th>1W</th><th>MTD</th><th>YTD</th></tr></thead>
<tbody>{_summary_rows(section_rows, type_order)}</tbody></table></div>
<div class="detail-label">Detalle por clasificación</div>
{_detail_sections(section_rows, type_order)}"""


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
h2{{font-size:17px;line-height:1.3;margin:0;color:#0f172a;letter-spacing:-.01em}}
.section-head{{display:flex;align-items:baseline;justify-content:space-between;margin:26px 2px 11px}}
.section-head span{{color:#64748b;font-size:11px;font-weight:500}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin-bottom:20px}}
.card-title{{font-size:14px;font-weight:600;color:#001e62;padding:15px 18px;border-bottom:1px solid #e2e8f0}}
.card-title .count{{float:right;color:#64748b;font-size:11px;font-weight:500}}
.detail-label{{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin:28px 2px 10px}}
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
<span class="brand">BTG Pactual</span><span class="date">Reporte diario</span>
<h1>Net New Money · Fondos</h1></div>
{_html_section("Fondos Mutuos", snapshot.report_date, snapshot.rows, REPORT_TYPES)}
{_html_section("Fondos de Inversión", snapshot.fi_report_date, snapshot.fi_rows, FI_REPORT_TYPES) if snapshot.fi_report_date and snapshot.fi_rows else ""}
<div class="note">FM: aportes − rescates · FI: flujo implícito por variación de cuotas · En FI no rescatables, las salidas son reducciones de cuotas, no rescates contractuales · Monedas por separado · Sin conversión FX · Fuente: CMF</div>
</div></body></html>"""


def send_report(
    snapshot: ReportSnapshot,
    settings: ReportSettings,
) -> str:
    delivery_date = datetime.now(SANTIAGO_TZ).date()
    fi_data_date = snapshot.fi_report_date or snapshot.report_date
    session = make_session(headers={
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": (
            f"fund-nnm-summary-v8-{delivery_date.isoformat()}-"
            f"fm-{snapshot.report_date.isoformat()}-"
            f"fi-{fi_data_date.isoformat()}"
        ),
    })
    try:
        response = session.post(
            RESEND_URL,
            json={
                "from": settings.sender,
                "to": list(settings.recipients),
                "subject": f"BTG | Net New Money Fondos | {delivery_date.isoformat()}",
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
        logger.info("Fund NNM emails are disabled")
        return DownloadResult(skipped=1)

    snapshot = load_snapshot()
    message_id = send_report(snapshot, settings)
    logger.info("Sent consolidated fund NNM report id=%s", message_id)
    return DownloadResult(downloaded=1)
