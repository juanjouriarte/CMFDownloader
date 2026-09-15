from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

from src.api.bolsa import btg_fixed_income_quotes, live_quote
from src.etl.bolsaSantiago.client import (
    BolsaAuthenticationError,
    BolsaQuoteNotFoundError,
    get_live_quote,
)
from src.etl.bolsaSantiago.quote_buffer import (
    BTG_FIXED_INCOME_FUNDS,
    BTGFixedIncomeQuoteBuffer,
    btg_fixed_income_quote_buffer,
)


def _quote(**overrides):
    data = {
        "nemotecnico": "CFICGSCH-A",
        "status": "two_sided",
        "bid_price": 129684.0,
        "bid_quantity": 83,
        "ask_price": 137789.0,
        "ask_quantity": 82,
        "midpoint": 133736.5,
        "spread": 8105.0,
        "spread_pct": 6.060425,
        "retrieved_at": datetime(2026, 9, 15, tzinfo=timezone.utc),
        "source": "Bolsa de Santiago",
    }
    data.update(overrides)
    return data


def test_client_extracts_best_bid_ask_and_spread():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "listaResult": [
            {"tipo_dato": "relacionados", "valor": 1},
            {
                "tipo_dato": "puntas",
                "compra": 129684,
                "cantidad_com": 83,
                "venta": 137789,
                "cantidad_ven": 82,
            },
        ]
    }
    session = MagicMock()
    session.post.return_value = response

    with patch(
        "src.etl.bolsaSantiago.client.make_bolsa_session",
        return_value=session,
    ):
        result = get_live_quote("CFICGSCH-A")

    assert result["status"] == "two_sided"
    assert result["bid_price"] == 129684.0
    assert result["bid_quantity"] == 83
    assert result["ask_price"] == 137789.0
    assert result["ask_quantity"] == 82
    assert result["midpoint"] == 133736.5
    assert result["spread"] == 8105.0
    assert result["spread_pct"] == 6.060425
    session.close.assert_called_once_with()


def test_client_maps_zero_prices_to_no_quotes():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "listaResult": [{
            "tipo_dato": "puntas",
            "compra": 0,
            "cantidad_com": 0,
            "venta": 0,
            "cantidad_ven": 0,
        }]
    }
    session = MagicMock()
    session.post.return_value = response

    with patch(
        "src.etl.bolsaSantiago.client.make_bolsa_session",
        return_value=session,
    ):
        result = get_live_quote("EMPTY")

    assert result["status"] == "no_quotes"
    assert result["bid_price"] is None
    assert result["ask_price"] is None
    assert result["spread"] is None


def test_client_rejects_expired_session_without_exposing_response():
    response = MagicMock(status_code=403)
    session = MagicMock()
    session.post.return_value = response

    with patch(
        "src.etl.bolsaSantiago.client.make_bolsa_session",
        return_value=session,
    ):
        with pytest.raises(BolsaAuthenticationError, match="expired"):
            get_live_quote("CFICGSCH-A")

    session.close.assert_called_once_with()


def test_api_normalizes_ticker_and_disables_caching():
    response = Response()
    with patch("src.api.bolsa.get_live_quote", return_value=_quote()) as fetch:
        result = live_quote(" cficgsch-a ", response)

    fetch.assert_called_once_with("CFICGSCH-A")
    assert result["bid_price"] == 129684.0
    assert response.headers["Cache-Control"] == "no-store"


def test_api_rejects_invalid_ticker_before_upstream_call():
    with patch("src.api.bolsa.get_live_quote") as fetch:
        with pytest.raises(HTTPException) as exc:
            live_quote("BAD/TICKER", Response())

    assert exc.value.status_code == 422
    fetch.assert_not_called()


def test_api_maps_missing_quote_to_404():
    with patch(
        "src.api.bolsa.get_live_quote",
        side_effect=BolsaQuoteNotFoundError("not found"),
    ):
        with pytest.raises(HTTPException) as exc:
            live_quote("UNKNOWN", Response())

    assert exc.value.status_code == 404


def test_fixed_income_buffer_refreshes_all_tickers_sequentially():
    calls = []

    def fetcher(ticker):
        calls.append(ticker)
        return _quote(nemotecnico=ticker)

    buffer = BTGFixedIncomeQuoteBuffer(
        fetcher=fetcher,
        pace_seconds=0,
        cache_seconds=60,
    )
    buffer.refresh_once()
    snapshot = buffer.snapshot()

    expected = [
        ticker
        for _, tickers in BTG_FIXED_INCOME_FUNDS
        for ticker in tickers
    ]
    assert calls == expected
    assert snapshot["status"] == "ready"
    assert snapshot["total_instruments"] == 14
    assert snapshot["available_quotes"] == 14
    assert snapshot["last_completed_at"] is not None
    assert [fund["name"] for fund in snapshot["funds"]] == [
        name for name, _ in BTG_FIXED_INCOME_FUNDS
    ]


def test_fixed_income_buffer_reuses_snapshot_until_cache_expires():
    calls = []

    def fetcher(ticker):
        calls.append(ticker)
        return _quote(nemotecnico=ticker)

    buffer = BTGFixedIncomeQuoteBuffer(
        fetcher=fetcher,
        pace_seconds=0,
        cache_seconds=60,
    )

    first = buffer.get_or_refresh()
    second = buffer.get_or_refresh()

    assert len(calls) == 14
    assert second["last_completed_at"] == first["last_completed_at"]

    buffer._last_completed_at -= timedelta(seconds=61)
    buffer.get_or_refresh()

    assert len(calls) == 28


def test_fixed_income_buffer_coalesces_concurrent_refreshes():
    calls = []
    first_fetch_started = threading.Event()
    release_fetch = threading.Event()

    def fetcher(ticker):
        calls.append(ticker)
        if len(calls) == 1:
            first_fetch_started.set()
            assert release_fetch.wait(timeout=2)
        return _quote(nemotecnico=ticker)

    buffer = BTGFixedIncomeQuoteBuffer(
        fetcher=fetcher,
        pace_seconds=0,
        cache_seconds=60,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(buffer.get_or_refresh)
        assert first_fetch_started.wait(timeout=2)
        second = executor.submit(buffer.get_or_refresh)
        release_fetch.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert len(calls) == 14


def test_fixed_income_endpoint_gets_or_refreshes_buffer():
    response = Response()
    buffered = {
        "status": "partial",
        "refreshing": False,
        "total_instruments": 14,
        "available_quotes": 13,
        "pace_seconds": 3.0,
        "cache_seconds": 60.0,
        "cache_expires_at": None,
        "cycle_started_at": None,
        "last_completed_at": None,
        "last_cycle_error": None,
        "funds": [],
    }
    with patch.object(
        btg_fixed_income_quote_buffer,
        "get_or_refresh",
        return_value=buffered,
    ) as get_or_refresh:
        result = btg_fixed_income_quotes(response)

    get_or_refresh.assert_called_once_with()
    assert result is buffered
    assert response.headers["Cache-Control"] == "no-store"


def test_fixed_income_endpoint_is_registered_in_openapi():
    from main import app

    assert "/bolsa/btg-fixed-income" in app.openapi()["paths"]
