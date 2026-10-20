"""TAC history must retain every series in each selected reporting period."""
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.api import mutual_funds


def test_tac_history_limits_periods_instead_of_series_rows(monkeypatch):
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE tac (run_fondo TEXT, periodo DATE, serie TEXT, tac_total REAL, tac_rem_fija REAL, tac_rem_var REAL, tac_gastos_op REAL)'))
        rows = [dict(run='test' if fund == 0 else 'other', period=date(2019 + month // 12, month % 12 + 1, 1), serie=serie)
                for fund in range(2) for month in range(85) for serie in ['A', 'B', 'C']]
        connection.execute(text('INSERT INTO tac VALUES (:run, :period, :serie, 1.5, 1.0, 0.3, 0.2)'), rows)
    monkeypatch.setattr(mutual_funds, 'SessionLocal', lambda: Session(engine))
    result = mutual_funds.get_fund_tac('test', None)
    assert len(result) == 24 * 3
    assert len({r.periodo for r in result}) == 24
    assert min(r.periodo for r in result) == date(2024, 2, 1)
    assert {r.serie for r in result if r.periodo == date(2025, 1, 1)} == {'A', 'B', 'C'}
    assert result[0].periodo == date(2026, 1, 1)
    assert mutual_funds.get_fund_tac('missing', None) == []
    full = mutual_funds.get_fund_tac('test', None, full_history=True)
    assert len(full) == 85 * 3
    assert min(r.periodo for r in full) == date(2019, 1, 1)
    assert mutual_funds.get_fund_tac('missing', None, full_history=True) == []
