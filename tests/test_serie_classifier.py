import pytest

from src.mutualFunds.loaders.nemotecnicos import classify_serie


# ---------------------------------------------------------------------------
# APV — from serie name (highest priority)
# ---------------------------------------------------------------------------

def test_apv_serie_name():
    assert classify_serie("APV", None) == "APV"


def test_apv_compound_serie_names():
    for serie in ["B-APV", "APV1", "APV2", "I-APV", "S-APV", "APV-APVC", "APVDIGITAL"]:
        assert classify_serie(serie, None) == "APV", f"Expected APV for serie={serie}"


def test_apv_serie_overrides_caracteristicas():
    # Even if caracteristicas says Institucional, APV serie wins
    assert classify_serie("APV", "APORTE EFECTUADO POR INVERSIONISTAS INSTITUCIONALES") == "APV"


# ---------------------------------------------------------------------------
# APV — from caracteristicas
# ---------------------------------------------------------------------------

def test_apv_from_ahorro_previsional():
    assert classify_serie("A", "Serie para constituir plan de Ahorro Previsional Voluntario") == "APV"


def test_apv_from_dl_3500():
    assert classify_serie("B", "Aportes efectuados en calidad de ahorro previsional voluntario en virtud de lo dispuesto en el D.L. 3.500") == "APV"


def test_apv_from_short_text():
    assert classify_serie("C", "APV.") == "APV"


# ---------------------------------------------------------------------------
# AFP
# ---------------------------------------------------------------------------

def test_afp_from_caracteristicas():
    assert classify_serie("A", "APORTES AFP O FONDOS DE PENSIONES") == "AFP"


def test_afp_admin_fondos_pensiones():
    assert classify_serie("I", "Aportes efectuados al Fondo por participes que sean Administradoras de Fondos de Pensiones y Fondos de Pensiones.") == "AFP"


# ---------------------------------------------------------------------------
# Institucional
# ---------------------------------------------------------------------------

def test_institucional_explicit():
    assert classify_serie("I", "APORTE EFECTUADO POR INVERSIONISTAS INSTITUCIONALES") == "Institucional"


def test_institucional_compañias_seguros():
    assert classify_serie("A", "Aportes realizados por Companias de Seguros de Vida y Generales") == "Institucional"


def test_institucional_corredores_bolsa():
    assert classify_serie("L", "Aportes realizados por Corredores de Bolsa para cartera propia") == "Institucional"


# ---------------------------------------------------------------------------
# Fondos (fund-of-funds series)
# ---------------------------------------------------------------------------

def test_fondos_otros_fondos():
    assert classify_serie("F", "APORTES EFECTUADOS POR OTROS FONDOS ADMINISTRADOS POR LA ADMINISTRADORA") == "Fondos"


def test_fondos_cartera_administrada():
    assert classify_serie("A", "APORTES EXCLUSIVOS DE CARTERA ADMINISTRADAS") == "Fondos"


def test_fondos_aporte_adm():
    assert classify_serie("A", "APORTE ADM DE CARTERA") == "Fondos"


# ---------------------------------------------------------------------------
# Digital
# ---------------------------------------------------------------------------

def test_digital_internet():
    assert classify_serie("WEB", "APORTES Y RESCATES REALIZADOS EXCLUSIVAMENTE A TRAVES DE INTERNET") == "Digital"


def test_digital_tyba():
    assert classify_serie("TYBA", "APORTE A TRAVEZ DE TYBA") == "Digital"


def test_digital_mach():
    assert classify_serie("A", "APORTES QUE SEAN EFECTUADOS A TRAVES DEL CANAL MACH OFRECIDO POR EL AGENTE") == "Digital"


# ---------------------------------------------------------------------------
# Empleados
# ---------------------------------------------------------------------------

def test_empleados_bci():
    assert classify_serie("A", "DESTINADA A PARTICIPES EMPLEADOS DE BANCO BCI O FILIALES") == "Empleados"


def test_empleados_administradora():
    assert classify_serie("A", "APORTES POR EMPLEADOS DE LA ADMINISTRADORA") == "Empleados"


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

def test_general_aportes_generales():
    assert classify_serie("A", "Aportes Generales") == "General"


def test_general_sin_monto_minimo():
    assert classify_serie("B", "DESTINADA A FINES DISTINTOS DE APV SIN MONTO MINIMO DE INGRESO") == "General"


def test_general_todo_tipo():
    assert classify_serie("A", "APORTE DE TODO TIPO DE INVERSIONISTA CON FINES DISTINTOS DE APV") == "General"


# ---------------------------------------------------------------------------
# None when nothing matches
# ---------------------------------------------------------------------------

def test_none_when_no_match():
    assert classify_serie("X", None) is None


def test_none_unknown_text():
    assert classify_serie("Z", "BALANCEADO GLOBAL PLUS SERIE A") is None


# ---------------------------------------------------------------------------
# Priority order
# ---------------------------------------------------------------------------

def test_afp_before_institucional():
    # AFP text should win over generic institucional
    caract = "APORTES EFECTUADOSEN UN MISMO DIA PORCOMPANIAS DE SEGUROS DE VIDA Y GENERALES TESORERIA GENERAL DE LA REPUBLICA ADMINISTRADORAS DE FONDOS DE PENSIONES"
    assert classify_serie("A", caract) == "AFP"
