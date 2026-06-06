import pandas as pd
import pytest

from src.mutualFunds.mutualFundsCategories import (
    _classify_debt,
    _classify_equity_geography,
)


# ---------------------------------------------------------------------------
# Equity geography
# ---------------------------------------------------------------------------

def _make_extr(data: list[tuple[str, float]]) -> pd.DataFrame:
    """Helper: list of (country_name, value) → DataFrame."""
    return pd.DataFrame({"pais_nombre": [d[0] for d in data], "valor": [d[1] for d in data]})


def test_accionario_eeuu_dominant():
    df = _make_extr([("USA", 90), ("Canada", 10)])
    assert _classify_equity_geography(df) == "FDOACCEEUU"


def test_accionario_eeuu_exactly_75_pct():
    df = _make_extr([("USA", 75), ("Japón", 25)])
    assert _classify_equity_geography(df) == "FDOACCEEUU"


def test_accionario_latam():
    df = _make_extr([("Brasil", 40), ("México", 30), ("Chile", 20), ("Perú", 10)])
    assert _classify_equity_geography(df) == "FDOACCALAT"


def test_accionario_europa_desarrollada():
    df = _make_extr([("Alemania", 40), ("Francia", 30), ("Reino Unido", 20), ("España", 10)])
    assert _classify_equity_geography(df) == "FDOACCEUR"


def test_accionario_desarrollado_no_dominant_region():
    df = _make_extr([("USA", 40), ("Japón", 30), ("Australia", 30)])
    assert _classify_equity_geography(df) == "FDOACCDES"


def test_accionario_emergente_no_dominant():
    df = _make_extr([("China", 40), ("India", 30), ("Brasil", 30)])
    assert _classify_equity_geography(df) == "FDOACCEME"


def test_accionario_asia_emergente():
    # 100% Asia Emergente but no single country hits 75% → classified as Emergente
    df = _make_extr([("China", 50), ("India", 30), ("Taiwán", 20)])
    assert _classify_equity_geography(df) == "FDOACCEME"


def test_empty_df_returns_desarrollado():
    assert _classify_equity_geography(pd.DataFrame()) == "FDOACCDES"


def test_fund_domicile_not_counted_as_market():
    # USA 76% weight → should classify as FDOACCEEUU when domicile rows excluded
    df = _make_extr([("USA", 76), (None, 14), ("Canada", 10)])
    assert _classify_equity_geography(df) == "FDOACCEEUU"


# ---------------------------------------------------------------------------
# Debt classification
# ---------------------------------------------------------------------------

def test_debt_nacional_uf_short():
    # 100% nacional, 100% UF, WAM 30 days
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, 30) == "RF<90NAC"


def test_debt_nacional_uf_medium():
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, 200) == "RF<365NUF"


def test_debt_nacional_clp_medium():
    assert _classify_debt(1.0, 0.0, 0.0, 1.0, 200) == "RF<365NCLP"


def test_debt_nacional_uf_long_under_3y():
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, 365 * 2) == "RF>365NUF<3"


def test_debt_nacional_uf_long_3_to_5y():
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, 365 * 4) == "RF>365NUF>3<5"


def test_debt_nacional_uf_long_over_5y():
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, 365 * 6) == "RF>365NUF>5"


def test_debt_nacional_clp_long():
    assert _classify_debt(1.0, 0.0, 0.0, 0.7, 500) == "RF>365NCLP"


def test_debt_international_short():
    assert _classify_debt(0.05, 0.95, 0.0, 0.0, 30) == "RF<90INTUSD"


def test_debt_international_medium():
    assert _classify_debt(0.05, 0.95, 0.0, 0.0, 200) == "RF<365INT"


def test_debt_international_long():
    assert _classify_debt(0.05, 0.95, 0.0, 0.0, 500) == "RF>365INTMINT"


def test_debt_flexible_mixed_origin():
    # Neither 100% nacional nor 60% internacional
    assert _classify_debt(0.55, 0.45, 0.5, 0.05, 500) == "RF>365OF"


def test_debt_no_wam_defaults_very_long():
    # None WAM → defaults to 9999 days (>5 years)
    assert _classify_debt(1.0, 0.0, 1.0, 0.0, None) == "RF>365NUF>5"
