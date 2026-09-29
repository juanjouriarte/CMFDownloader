from unittest.mock import patch

from src.api.admins import AdminItem
from src.api.categories import _build_catalog
from src.api.industry import industry_evolution, industry_funds


def test_category_catalog_builds_type_group_hierarchy():
    rows = [
        {"tipo": "Alternativo", "grupo": "Capital Privado", "code": "FI_PE",
         "name": "PE / Buyout", "fund_count": 42},
        {"tipo": "Alternativo", "grupo": "Capital Privado", "code": "FI_VC",
         "name": "Venture Capital", "fund_count": 12},
        {"tipo": "Deuda", "grupo": "Deuda Nacional", "code": "FI_DN_90",
         "name": "Deuda Nacional ≤ 90 días", "fund_count": 20},
    ]

    catalog = _build_catalog(rows)

    assert catalog[0]["type"] == "Alternativo"
    assert catalog[0]["groups"][0]["group"] == "Capital Privado"
    assert [x["code"] for x in catalog[0]["groups"][0]["categories"]] == ["FI_PE", "FI_VC"]
    assert catalog[1]["type"] == "Deuda"


def test_admin_item_allows_missing_rut():
    item = AdminItem(
        rut=None,
        nombre="ADMIN SIN RUT",
        funds_fm=0,
        funds_fm_vigente=0,
        funds_fi=2,
        funds_fi_vigente=2,
        funds_total=2,
    )
    assert item.rut is None


def test_fund_screener_applies_requested_filters():
    with patch("src.api.industry._rows", return_value=[]) as rows:
        industry_funds(
            pagination=(25, 10),
            _=None,
            fund_type="fi",
            type="Alternativo",
            group="Capital Privado",
            category="FI_PE",
            admin="BTG",
            rescatable=False,
            vigente=True,
            sort="nnm",
        )

    sql, params = rows.call_args.args
    assert "ORDER BY currency, nnm_ytd DESC" in sql
    assert params["fund_type"] == "fi"
    assert params["category"] == "FI_PE"
    assert params["rescatable"] is False
    assert params["limit"] == 25
    assert params["offset"] == 10


def test_evolution_limits_source_scans_to_requested_dates():
    with patch("src.api.industry._rows", return_value=[]) as rows:
        industry_evolution(
            _=None,
            fund_type="fm",
            group_by="admin",
            from_date=None,
            to_date=None,
        )

    sql, params = rows.call_args.args
    assert "WHERE cd.fecha >= COALESCE(CAST(:from_date AS date)" in sql
    assert "WHERE v.fecha >= COALESCE(CAST(:from_date AS date)" in sql
    assert "COALESCE(administrator, 'Sin clasificar')" in sql
    assert params["fund_type"] == "fm"


def test_overview_aggregates_one_currency_and_reads_snapshot_once():
    from datetime import date
    from decimal import Decimal
    from src.api.industry import industry_overview
    sample = [
        dict(fund_type=kind, run_fondo=str(i), administrator=admin, currency="USD",
             aum=Decimal(aum), data_date=date(2026, 9, 14), nnm_month=Decimal("2"),
             nnm_ytd=Decimal("10"), aportes_month=Decimal("5") if kind == "fm" else None,
             rescates_month=Decimal("3") if kind == "fm" else None,
             category_type="Deuda", category_group="Nacional", category="RF", category_name="Deuda")
        for i, (kind, admin, aum) in enumerate([("fm", "BTG", "100"), ("fi", "OTHER", "300")])
    ]
    with patch("src.api.industry._rows", return_value=sample) as rows:
        result = industry_overview(None, currency="USD")
    assert rows.call_count == 1
    assert rows.call_args.args[1]["currency"] == "USD"
    assert "v_rentabilidad_fm_quality" not in rows.call_args.args[0]
    assert result["total_aum"] == Decimal("400")
    assert result["total_aum_clp"] is None
    assert result["neto_month"] == Decimal("2")  # FM gross-flow panel excludes FI
    assert result["btg_administrator"]["market_share_pct"] == 25
    assert result["btg_administrator"]["aum_clp"] is None
    assert sum(r["aum"] for r in result["breakdown"]) == result["total_aum"]


def test_overview_empty_currency_does_not_invent_zero_aum():
    from src.api.industry import industry_overview
    with patch("src.api.industry._rows", return_value=[]):
        result = industry_overview(None, currency="EUR")
    assert result["total_aum"] is None
    assert result["neto_month"] is None
    assert result["active_funds"] == 0
    assert result["btg_administrator"] is None


def test_monthly_overview_does_not_scan_or_mislabel_ytd_flows():
    from src.api.industry import industry_overview
    with patch("src.api.industry._rows", return_value=[]) as rows:
        result = industry_overview(None, currency="CLP", include_ytd=False)
    sql = rows.call_args.args[0]
    assert "DATE_TRUNC('year', ref.fecha)" not in sql
    assert "cd.fecha >= DATE_TRUNC('month', ref.fecha)" in sql
    assert "v.fecha >= DATE_TRUNC('month', ref.fecha)" in sql
    assert "event_date >= DATE_TRUNC('month', ref.fecha)" in sql
    assert "NULL::numeric AS nnm_ytd" in sql
    assert result["includes_ytd"] is False
    assert result["net_flow_ytd"] is None


def test_overview_keeps_ytd_enabled_for_existing_api_consumers():
    from src.api.industry import industry_overview
    with patch("src.api.industry._rows", return_value=[]) as rows:
        result = industry_overview(None)
    assert "cd.fecha >= DATE_TRUNC('year', ref.fecha)" in rows.call_args.args[0]
    assert result["includes_ytd"] is True


def test_evolution_separates_currency_and_sums_monthly_flows():
    from src.api.industry import industry_evolution
    with patch("src.api.industry._rows", return_value=[dict(aum=12, nnm=2)]) as rows:
        result = industry_evolution(None, currency="EUR", nombre_cat="Deuda", admin="BTG")
    sql, params = rows.call_args.args
    assert params["currency"] == "EUR"
    assert params["nombre_cat"] == "Deuda"
    assert params["admin"] == "%BTG%"
    assert "SUM(reported_nnm) AS nnm" in sql
    assert "ARRAY_AGG(aum ORDER BY data_date DESC)" in sql
    assert result[0]["aum_clp"] is None
    assert result[0]["aum"] == 12


def test_legacy_clp_aliases_remain_clp_only():
    from src.api.industry import _legacy
    assert _legacy(dict(aum=123, nnm=7), "CLP")["aum_clp"] == 123
    assert _legacy(dict(aum=123, nnm=7), "USD")["aum_clp"] is None


def test_industry_rejects_combined_currency():
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as client:
        for endpoint in ("overview", "evolution", "funds"):
            result = client.get(f"/industry/{endpoint}?currency=all")
            assert result.status_code == 422


def test_evolution_uses_current_monthly_history_for_calendar_ranges():
    from datetime import date
    with patch('src.api.industry._monthly_history_is_current', return_value=True) as fresh, \
         patch('src.api.industry._rows', return_value=[]) as rows:
        industry_evolution(None, from_date=date(2024, 10, 1), currency='USD', admin='BTG')
    sql, params = rows.call_args.args
    assert 'FROM mv_industry_monthly_fm' in sql
    assert 'FROM mv_industry_monthly_fi' in sql
    assert 'FROM cartola_diaria' not in sql
    assert params['currency'] == 'USD'
    assert params['admin'] == '%BTG%'
    assert 'fm_flow_adjustments' in sql  # corrections are applied live
    fresh.assert_called_once_with('all')


def test_evolution_falls_back_when_monthly_history_is_stale():
    from datetime import date
    with patch('src.api.industry._monthly_history_is_current', return_value=False), \
         patch('src.api.industry._rows', return_value=[]) as rows:
        industry_evolution(None, from_date=date(2024, 10, 1))
    assert 'FROM cartola_diaria' in rows.call_args.args[0]


def test_evolution_retains_exact_partial_month_ranges():
    from datetime import date
    for start, end in [(date(2024, 10, 15), None), (date(2024, 10, 1), date(2025, 1, 15))]:
        with patch('src.api.industry._monthly_history_is_current') as fresh, \
             patch('src.api.industry._rows', return_value=[]) as rows:
            industry_evolution(None, from_date=start, to_date=end)
        fresh.assert_not_called()
        assert 'FROM cartola_diaria' in rows.call_args.args[0]
