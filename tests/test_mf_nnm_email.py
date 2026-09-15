from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.reports.mf_nnm_email import (
    CategoryFlow,
    ReportSettings,
    ReportSnapshot,
    _html_report,
    _idempotency_key,
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
                daily=Decimal("1500000000"),
                mtd=Decimal("2000000000"),
                ytd=Decimal("-3000000000"),
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
    plain = _plain_report("Accionario", snapshot)
    rendered_html = _html_report("Accionario", snapshot)

    assert "1.50 bn CLP" in plain
    assert "-3.00 bn CLP" in plain
    assert "Accionario Nacional &lt;Large Cap&gt;" in rendered_html
    assert "Accionario Nacional <Large Cap>" not in rendered_html


def test_empty_high_level_report_is_explicit(snapshot):
    assert "No funds are currently assigned" in _plain_report("Estructurado", snapshot)
    assert "No funds are currently assigned" in _html_report("Estructurado", snapshot)


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
    with patch("src.reports.mf_nnm_email.make_session", return_value=session) as factory:
        assert send_report("Accionario", snapshot, settings) == "email-123"

    response.raise_for_status.assert_called_once_with()
    headers = factory.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Idempotency-Key"] == "mf-nnm-2026-09-14-accionario"
    request = session.post.call_args
    assert request.kwargs["json"]["to"] == ["recipient@example.com"]
    session.close.assert_called_once_with()


def test_disabled_job_does_not_query_or_send(monkeypatch):
    monkeypatch.setenv("NNM_EMAIL_ENABLED", "false")
    with patch("src.reports.mf_nnm_email.load_snapshot") as load:
        result = run()

    load.assert_not_called()
    assert result.skipped == 5


def test_idempotency_key_is_stable():
    assert _idempotency_key("Inversionistas Calificados", date(2026, 9, 14)) == (
        "mf-nnm-2026-09-14-inversionistas-calificados"
    )
