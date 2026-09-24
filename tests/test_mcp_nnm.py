from datetime import date
from decimal import Decimal

import pytest

import src.mcp_server as server


def test_fm_nnm_keeps_reported_and_adjusted_values_and_attaches_contributors(monkeypatch):
    aggregate = {
        "administrator": "SANTANDER ASSET MANAGEMENT S.A. ADMINISTRADORA GENERAL DE FONDOS",
        "currency": "CLP",
        "rescatable": None,
        "aportes_millions": Decimal("530.00"),
        "rescates_millions": Decimal("30.00"),
        "reported_nnm_millions": Decimal("500.00"),
        "internal_migrations_millions": Decimal("400.00"),
        "adjusted_nnm_millions": Decimal("100.00"),
        "fund_count": 2,
        "from_date": date(2026, 1, 1),
        "to_date": date(2026, 9, 14),
        "data_as_of": date(2026, 9, 14),
        "classification_as_of": date(2026, 8, 31),
    }
    contributor = {
        "administrator": aggregate["administrator"],
        "currency": "CLP",
        "rescatable": None,
        "run_fondo": "33072",
        "fund_name": "SANTANDER ACCIONES SELECTAS",
        "reported_nnm_millions": Decimal("500.00"),
        "internal_migrations_millions": Decimal("400.00"),
        "adjusted_nnm_millions": Decimal("100.00"),
        "contributor_rank": 1,
    }
    calls = []

    def fake_rows(sql, params):
        calls.append((sql, params))
        return [aggregate.copy()] if len(calls) == 1 else [contributor.copy()]

    monkeypatch.setattr(server, "_rows", fake_rows)
    monkeypatch.setattr(server, "_scalar", lambda *_args, **_kwargs: date(2026, 9, 14))

    response = server.net_new_money_ranking(
        period="ytd",
        fund_type="fm",
        group_by="agf",
        administrator="Santander",
        category_type="Accionario",
        currency="clp",
        include_contributors=True,
    )

    assert response["data_as_of"] == date(2026, 9, 14)
    assert response["classification_as_of"] == date(2026, 8, 31)
    assert response["filters"]["currency"] == "CLP"
    assert response["results"][0]["reported_nnm_millions"] == Decimal("500.00")
    assert response["results"][0]["adjusted_nnm_millions"] == Decimal("100.00")
    assert response["results"][0]["contributors"][0]["run_fondo"] == "33072"
    assert "PARTITION BY administrator, currency, rescatable" in calls[1][0]
    assert "FROM cartola_diaria c" in calls[0][0]
    assert "FROM fm_daily_flows_adjusted" not in calls[0][0]
    assert calls[0][1]["administrator"] == "%Santander%"


def test_nnm_query_groups_by_currency_and_anchors_ytd_to_latest_data(monkeypatch):
    captured = {}

    def fake_rows(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(server, "_rows", fake_rows)
    monkeypatch.setattr(server, "_scalar", lambda *_args, **_kwargs: date(2026, 9, 14))

    response = server.net_new_money_ranking(period="ytd", fund_type="fm")

    assert "administrator, currency, rescatable" in captured["sql"]
    assert "PARTITION BY currency, rescatable" in captured["sql"]
    assert "DATE_TRUNC('year', b.data_as_of)" in captured["sql"]
    assert "CURRENT_DATE" not in captured["sql"]
    assert response["units"].startswith("millions")


def test_fi_nnm_includes_rescatable_and_non_rescatable_sources(monkeypatch):
    captured = {}

    def fake_rows(sql, params):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(server, "_rows", fake_rows)
    monkeypatch.setattr(server, "_scalar", lambda *_args, **_kwargs: date(2026, 9, 14))

    server.net_new_money_ranking(fund_type="fi", group_by="category", period="last_3m")

    assert "FROM valores_cuota_fi v" in captured["sql"]
    assert "FROM deltas d" in captured["sql"]
    assert "UNION ALL" in captured["sql"]
    assert "category_name, currency, rescatable" in captured["sql"]


def test_nnm_rejects_inverted_custom_date_range():
    with pytest.raises(ValueError, match="from_date"):
        server.net_new_money_ranking(from_date="2026-09-02", to_date="2026-09-01")
