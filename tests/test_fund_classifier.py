from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.etl.mutualFunds.mutualFundsCategories import (
    _classify_debt,
    _classify_equity_geography,
    run as _classify_fm,
)
from src.etl.investmentFunds.investmentFundsCategories import (
    _add_maturity_days,
    _classify as _classify_fi,
    _debt_sub as _classify_fi_debt,
    _weighted_maturity,
    _equity_sub,
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


def test_otroc_capitalization_instruments_count_as_national_equity():
    session = MagicMock()
    session.execute.side_effect = [
        MagicMock(fetchall=lambda: [
            ("8898", "ACC", "$$", None, "8873"),
            ("8898", "OTROC", "$$", None, "880"),
            ("8898", "CFI", "$$", None, "246"),
        ]),
        MagicMock(fetchall=lambda: []),
        MagicMock(fetchall=lambda: [
            ("8898", "FONDO MUTUO BTG PACTUAL CHILE ACCIÓN"),
        ]),
    ]
    session_context = MagicMock()
    session_context.__enter__.return_value = session

    with patch(
        "src.etl.mutualFunds.mutualFundsCategories.SessionLocal",
        return_value=session_context,
    ):
        result = _classify_fm(date(2026, 8, 1))

    chile_accion = result.iloc[0]
    assert chile_accion["pct_equity"] == pytest.approx(97.5)
    assert chile_accion["categoria"] == "FDOACCNACLC"
    assert chile_accion["tipo"] == "Accionario"


def test_fi_national_equity_35_pct_ipsa_is_small_mid_cap():
    assert _equity_sub(True, "Fondo Nacional", 0.35) == "FI_ACC_NAC_SC"


def test_fi_national_equity_above_35_pct_ipsa_is_general():
    assert _equity_sub(True, "Fondo Nacional", 0.351) == "FI_ACC_NAC"


def test_fi_national_equity_65_pct_ipsa_is_large_cap():
    assert _equity_sub(True, "Fondo Nacional", 0.65) == "FI_ACC_NAC_LC"


def test_fi_debt_national_short_term():
    assert _classify_fi_debt(
        0.80, 0.20, wam_nac=75, pct_nac_clp=0.70
    ) == "FI_DN_90"


def test_fi_debt_national_uf_three_to_five_years():
    assert _classify_fi_debt(
        0.75, 0.25, wam_nac=365 * 4, pct_nac_uf=0.80
    ) == "FI_DN_LP_UF5"


def test_fi_debt_international_long_term():
    assert _classify_fi_debt(
        0.20, 0.80, wam_ext=500
    ) == "FI_DI_LP"


def test_fi_debt_flexible_origin():
    assert _classify_fi_debt(
        0.50, 0.50, wam_all=200
    ) == "FI_DF_365"


def test_fi_debt_without_enough_maturity_data_is_not_guessed():
    assert _classify_fi_debt(0.90, 0.10) == "FI_DEUDA_ND"


def test_fi_vehicle_without_maturity_uses_private_equity_name():
    category, confidence = _classify_fi(
        0, 0, 0, 0, 0, 0, 0.99, 0, 0.01,
        False, "LARRAIN VIAL PRIVATE EQUITY VIII", False,
    )

    assert category == "FI_PE"
    assert confidence == "Media"


def test_fi_vehicle_without_maturity_uses_private_debt_name():
    category, confidence = _classify_fi(
        0, 0, 0, 0, 0, 0, 0.99, 0, 0.01,
        False, "PRIVATE MARKETS DEUDA EVERGREEN", False,
    )

    assert category == "FI_DEUDA_PRIVADA"
    assert confidence == "Media"


def test_fi_non_rescatable_vehicle_without_maturity_falls_back_to_private_equity():
    category, confidence = _classify_fi(
        0, 0, 0, 0, 0, 0, 0.99, 0, 0.01,
        False, "FONDO INTERNACIONAL VIII", False,
    )

    assert category == "FI_PE"
    assert confidence == "Baja"


def test_fi_weighted_maturity_uses_portfolio_weights():
    positions = pd.DataFrame({
        "pct": [75.0, 25.0],
        "fecha_vencimiento": ["30/04/2026", "30/06/2026"],
    })
    _add_maturity_days(positions, date(2026, 3, 31))

    assert _weighted_maturity(positions) == pytest.approx(45.25)


def test_fi_weighted_maturity_requires_half_of_weight_covered():
    positions = pd.DataFrame({
        "pct": [40.0, 60.0],
        "fecha_vencimiento": ["30/04/2026", None],
    })
    _add_maturity_days(positions, date(2026, 3, 31))

    assert _weighted_maturity(positions) is None


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
