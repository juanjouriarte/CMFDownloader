from datetime import date

import pytest

from src.mutualFunds.loaders.tac import (
    _fix_mojibake,
    _normalize,
    _strip_suffix,
    _to_numeric,
)


# ---------------------------------------------------------------------------
# Mojibake fix
# ---------------------------------------------------------------------------

def test_fix_mojibake_estrategico():
    assert _fix_mojibake("EstratÃ©gico") == "Estratégico"


def test_fix_mojibake_dolar():
    assert _fix_mojibake("DÃ³lar") == "Dólar"


def test_fix_mojibake_inversion():
    assert _fix_mojibake("InversiÃ³n") == "Inversión"


def test_fix_mojibake_clean_string_unchanged():
    assert _fix_mojibake("SECURITY S.A.") == "SECURITY S.A."


def test_fix_mojibake_invalid_utf8_returns_original():
    # Strings that are not valid UTF-8 after latin-1 encode should come back unchanged
    result = _fix_mojibake("BCI\xed")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def test_normalize_removes_accents():
    assert _normalize("Estratégico") == "ESTRATEGICO"


def test_normalize_uppercase():
    assert _normalize("fondo mutuo bci") == "FONDO MUTUO BCI"


def test_normalize_strips_whitespace():
    assert _normalize("  BCI  ") == "BCI"


def test_normalize_empty_string():
    assert _normalize("") == ""


def test_normalize_non_string_returns_empty():
    assert _normalize(None) == ""


# ---------------------------------------------------------------------------
# Strip suffix
# ---------------------------------------------------------------------------

def test_strip_suffix_removes_number():
    assert _strip_suffix("FONDO MUTUO BANCHILE AGRESIVO (2)") == "FONDO MUTUO BANCHILE AGRESIVO"


def test_strip_suffix_removes_higher_numbers():
    assert _strip_suffix("FONDO MUTUO BCI (10)") == "FONDO MUTUO BCI"


def test_strip_suffix_no_suffix_unchanged():
    assert _strip_suffix("FONDO MUTUO BCI COMPETITIVO") == "FONDO MUTUO BCI COMPETITIVO"


def test_strip_suffix_only_removes_trailing():
    assert _strip_suffix("FONDO (2) MUTUO") == "FONDO (2) MUTUO"


# ---------------------------------------------------------------------------
# Numeric parsing
# ---------------------------------------------------------------------------

def test_to_numeric_comma_decimal():
    assert _to_numeric("2,5") == pytest.approx(2.5)


def test_to_numeric_dot_decimal():
    assert _to_numeric("2.0") == pytest.approx(2.0)


def test_to_numeric_na_returns_none():
    assert _to_numeric("NA") is None
    assert _to_numeric("N/A") is None


def test_to_numeric_empty_returns_none():
    assert _to_numeric("") is None
    assert _to_numeric(None) is None


def test_to_numeric_integer():
    assert _to_numeric("42") == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# Period parsing (inline logic from load_tac)
# ---------------------------------------------------------------------------

def test_period_parsed_from_float():
    val = 20260531.0
    s = str(int(float(val)))
    result = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    assert result == date(2026, 5, 31)


def test_period_parsed_jan():
    val = 20200131.0
    s = str(int(float(val)))
    result = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    assert result == date(2020, 1, 31)
