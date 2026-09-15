from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

from src.api.bolsa import live_quote
from src.etl.bolsaSantiago.client import (
    BolsaAuthenticationError,
    BolsaQuoteNotFoundError,
    get_live_quote,
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
