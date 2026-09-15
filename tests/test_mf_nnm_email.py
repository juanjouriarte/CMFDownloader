from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.reports.mf_nnm_email import (
    CategoryFlow,
    CurrencyFlow,
    ReportSettings,
    ReportSnapshot,
    _html_report,
    _plain_report,
    run,
    send_report,
)


@pytest.fixture
def snapshot():
    return ReportSnapshot(
        report_date=date(2026, 9, 14),
        rows=(
            CategoryFlow(
                tipo="Accionario",
                categoria="FDOACCNACLC",
                nombre="Accionario Nacional <Large Cap>",
                funds=4,
                currencies=(
                    CurrencyFlow(
                        currency="CLP",
                        daily=Decimal("1500000000"),
                        week=Decimal("1750000000"),
                        mtd=Decimal("2000000000"),
                        ytd=Decimal("-3000000000"),
                    ),
                    CurrencyFlow(
                        currency="USD",
                        daily=Decimal("2500000"),
                        week=Decimal("3000000"),
                        mtd=Decimal("4000000"),
                        ytd=Decimal("5000000"),
                    ),
                ),
                unsupported_currency_rows=0,
            ),
        ),
        fi_report_date=date(2026, 9, 13),
        fi_rows=(
            CategoryFlow(
                tipo="Alternativo",
                categoria="FI_DEUDA_PRIVADA",
                nombre="Deuda Privada",
                funds=2,
                currencies=(
                    CurrencyFlow(
                        currency="CLP",
                        daily=Decimal("500000000"),
                        week=Decimal("750000000"),
                        mtd=Decimal("900000000"),
                        ytd=Decimal("1200000000"),
                    ),
                    CurrencyFlow(
                        currency="COP",
                        daily=Decimal("2500000"),
                        week=Decimal("3000000"),
                        mtd=Decimal("4000000"),
                        ytd=Decimal("5000000"),
                    ),
                ),
                unsupported_currency_rows=0,
            ),
            CategoryFlow(
                tipo="Accionario",
                categoria="FI_ACC_NAC_SC",
                nombre="RV Nacional Small/Mid Cap",
                funds=3,
                currencies=(
                    CurrencyFlow(
                        currency="CLP",
                        daily=Decimal("800000000"),
                        week=Decimal("1100000000"),
                        mtd=Decimal("1400000000"),
                        ytd=Decimal("2100000000"),
                    ),
                    CurrencyFlow(
                        currency="EUR",
                        daily=Decimal("1000000"),
                        week=Decimal("1200000"),
                        mtd=Decimal("1500000"),
                        ytd=Decimal("2000000"),
                    ),
                ),
                unsupported_currency_rows=0,
            ),
        ),
    )


def test_settings_validate_enabled_configuration(monkeypatch):
    monkeypatch.setenv("NNM_EMAIL_ENABLED", "true")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("NNM_EMAIL_RECIPIENTS", raising=False)

    with pytest.raises(ValueError, match="RESEND_API_KEY.*NNM_EMAIL_RECIPIENTS"):
        ReportSettings.from_env()


def test_report_renders_plain_text_and_escaped_html(snapshot):
    plain = _plain_report(snapshot)
    rendered_html = _html_report(snapshot)

    assert "1.50 bn CLP" in plain
    assert "2.50 mm USD" in plain
    assert "-3.00 bn CLP" in plain
    assert "Accionario Nacional &lt;Large Cap&gt;" in rendered_html
    assert "Accionario Nacional <Large Cap>" not in rendered_html
    assert "BTG Pactual" in rendered_html
    assert "Net New Money · Fondos" in rendered_html
    assert "Fondos de Inversión" in rendered_html
    assert "Fondos de Inversión Rescatables" not in rendered_html
    assert "Fondos de Inversión No Rescatables" not in rendered_html
    assert "2.50 mm COP" in rendered_html
    assert "1.00 mm EUR" in rendered_html
    assert "Datos al 2026-09-13" in rendered_html
    assert "Clasificación general" in rendered_html
    assert "Detalle por clasificación" in rendered_html
    assert ">1W<" in rendered_html
    assert rendered_html.count('class="card detail-card"') == 3
    assert "4 fondos" in rendered_html


def test_inactive_high_level_types_are_omitted(snapshot):
    assert "Estructurado" not in _plain_report(snapshot)
    assert "Estructurado" not in _html_report(snapshot)


def test_send_report_uses_resend_and_idempotency(snapshot):
    settings = ReportSettings(
        enabled=True,
        api_key="test-key",
        sender="CMF Reports <onboarding@resend.dev>",
        recipients=("recipient@example.com",),
    )
    response = MagicMock()
    response.json.return_value = {"id": "email-123"}

    session = MagicMock()
    session.post.return_value = response
    with (
        patch("src.reports.mf_nnm_email.make_session", return_value=session) as factory,
        patch("src.reports.mf_nnm_email.datetime") as clock,
    ):
        clock.now.return_value.date.return_value = date(2026, 9, 15)
        assert send_report(snapshot, settings) == "email-123"

    response.raise_for_status.assert_called_once_with()
    headers = factory.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Idempotency-Key"] == (
        "fund-nnm-summary-v8-2026-09-15-fm-2026-09-14-fi-2026-09-13"
    )
    request = session.post.call_args
    assert request.kwargs["json"]["to"] == ["recipient@example.com"]
    assert request.kwargs["json"]["subject"] == (
        "BTG | Net New Money Fondos | 2026-09-15"
    )
    session.close.assert_called_once_with()


def test_disabled_job_does_not_query_or_send(monkeypatch):
    monkeypatch.setenv("NNM_EMAIL_ENABLED", "false")
    with patch("src.reports.mf_nnm_email.load_snapshot") as load:
        result = run()

    load.assert_not_called()
    assert result.skipped == 1
