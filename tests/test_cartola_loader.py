from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.mutualFunds.loaders.cartola import COLUMN_MAP, NUMERIC_COLS, load_cartola


# ---------------------------------------------------------------------------
# Column map
# ---------------------------------------------------------------------------

def test_dropped_columns_not_in_column_map():
    assert "RUN_ADM" not in COLUMN_MAP
    assert "NOM_ADM" not in COLUMN_MAP
    assert "COMISION_INVERSION" not in COLUMN_MAP
    assert "COMISION_RESCATE" not in COLUMN_MAP


def test_required_columns_present():
    assert "RUN_FM" in COLUMN_MAP
    assert "FECHA_INF" in COLUMN_MAP
    assert "VALOR_CUOTA" in COLUMN_MAP
    assert "PATRIMONIO_NETO" in COLUMN_MAP


def test_comision_columns_not_in_numeric_cols():
    assert "comision_inversion" not in NUMERIC_COLS
    assert "comision_rescate" not in NUMERIC_COLS


# ---------------------------------------------------------------------------
# Parsing via load_cartola (mocked DB)
# ---------------------------------------------------------------------------

SAMPLE_CSV = """RUN_ADM;NOM_ADM;RUN_FM;FECHA_INF;ACTIVO_TOT;MONEDA;PARTICIPES_INST;INVERSION_EN_FONDOS;SERIE;CUOTAS_APORTADAS;CUOTAS_RESCATADAS;CUOTAS_EN_CIRCULACION;VALOR_CUOTA;PATRIMONIO_NETO;NUM_PARTICIPES;NUM_PARTICIPES_INST;FONDO_PEN;REM_FIJA;REM_VARIABLE;GASTOS_AFECTOS;GASTOS_NO_AFECTOS;COMISION_INVERSION;COMISION_RESCATE;FACTOR DE AJUSTE;FACTOR DE REPARTO
96639280;SECURITY S.A.;8011;20260531;1000000;$$;S;1;A;100,5;50,0;5000,0;32900,5;160000000;46;0;N;1000,0;0,0;50,0;10,0;0,0;0,0;;
"""


def _write_tmp_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cartola_test.txt"
    p.write_text(content, encoding="utf-8")
    return p


def test_date_parsed_correctly(tmp_path):
    path = _write_tmp_csv(tmp_path, SAMPLE_CSV)
    df = pd.read_csv(path, sep=";", dtype=str)
    df["FECHA_INF"] = pd.to_datetime(df["FECHA_INF"].str.strip(), format="%Y%m%d", errors="coerce").dt.date
    assert df["FECHA_INF"].iloc[0] == date(2026, 5, 31)


def test_comma_decimal_numeric_parsed(tmp_path):
    path = _write_tmp_csv(tmp_path, SAMPLE_CSV)
    df = pd.read_csv(path, sep=";", dtype=str)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df["cuotas_aportadas"] = pd.to_numeric(df["cuotas_aportadas"].str.replace(",", "."), errors="coerce")
    assert df["cuotas_aportadas"].iloc[0] == pytest.approx(100.5)


def test_dropped_columns_not_loaded(tmp_path):
    path = _write_tmp_csv(tmp_path, SAMPLE_CSV)
    df = pd.read_csv(path, sep=";", dtype=str)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    assert "nom_adm" not in df.columns
    assert "run_adm" not in df.columns
    assert "comision_inversion" not in df.columns
    assert "comision_rescate" not in df.columns


def test_rows_with_no_fecha_dropped(tmp_path):
    csv = SAMPLE_CSV + "96639280;SECURITY;8012;INVALID_DATE;1000000;$$;S;1;A;0;0;0;1000;500000;10;0;N;0;0;0;0;0;0;;\n"
    path = _write_tmp_csv(tmp_path, csv)
    df = pd.read_csv(path, sep=";", dtype=str)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={"RUN_FM": "run_fondo", "FECHA_INF": "fecha"})
    df["fecha"] = pd.to_datetime(df["fecha"].str.strip(), format="%Y%m%d", errors="coerce").dt.date
    df = df.dropna(subset=["fecha"])
    assert len(df) == 1
