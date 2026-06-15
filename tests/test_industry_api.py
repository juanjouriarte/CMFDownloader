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
        {"tipo": "Deuda", "grupo": "Deuda", "code": "FI_DEUDA_NAC",
         "name": "Deuda Nacional", "fund_count": 20},
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
    assert "ORDER BY nnm_ytd_clp DESC" in sql
    assert "fund_type = :fund_type" in sql
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
