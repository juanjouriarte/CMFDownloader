from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models.aportantes_fi import AportanteFI, CuotasFI
from src.etl.investmentFunds.downloaders import aportantesDownloader as downloader

PERIOD = date(2026, 6, 30)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    CuotasFI.__table__.create(engine)
    AportanteFI.__table__.create(engine)
    monkeypatch.setattr(downloader, 'SessionLocal', lambda: Session(engine))
    yield engine
    engine.dispose()


def add_rows(db, cuotas=False, shareholders=False, run='7173', period=PERIOD):
    with Session(db) as session:
        if cuotas:
            session.add(CuotasFI(run_fondo=run, periodo=period, cuotas_pagadas=100))
        if shareholders:
            session.add(AportanteFI(id=1, run_fondo=run, periodo=period, rank=1))
        session.commit()


@pytest.mark.parametrize('cuotas,shareholders,expected', [
    (False, False, False), (True, False, False), (False, True, False), (True, True, True),
])
def test_skip_requires_both_datasets(db, cuotas, shareholders, expected):
    add_rows(db, cuotas, shareholders)
    assert downloader._already_loaded('7173', PERIOD) is expected


@pytest.mark.parametrize('run,period', [('9999', PERIOD), ('7173', date(2026, 3, 31))])
def test_shareholders_for_another_fund_or_quarter_do_not_complete_import(db, run, period):
    add_rows(db, cuotas=True)
    add_rows(db, shareholders=True, run=run, period=period)
    assert not downloader._already_loaded('7173', PERIOD)


def test_cuota_only_filing_is_fetched_without_force(db, monkeypatch):
    add_rows(db, cuotas=True)
    fetch = Mock(return_value=SimpleNamespace(text='source'))
    load = Mock(return_value=2)
    monkeypatch.setattr(downloader, 'fetch', fetch)
    monkeypatch.setattr(downloader, 'parse_html', lambda *_: ([{'rank': 1}], {'run_fondo': '7173'}))
    monkeypatch.setattr(downloader, 'load_aportantes', load)
    monkeypatch.setattr(downloader, 'mark_has_data', Mock())
    monkeypatch.setattr(downloader.time, 'sleep', lambda _: None)
    fund = SimpleNamespace(run_fondo='7173', rescatable=True, vigente=True)
    result = downloader._fetch_fund(fund, [(2026, 6)], fast=False, force=False)
    assert result.downloaded == 1
    assert result.rows_upserted == 2
    fetch.assert_called_once()
    load.assert_called_once()


def test_complete_import_does_not_fetch_again(db, monkeypatch):
    add_rows(db, cuotas=True, shareholders=True)
    fetch = Mock()
    monkeypatch.setattr(downloader, 'fetch', fetch)
    fund = SimpleNamespace(run_fondo='7173', rescatable=True, vigente=True)
    result = downloader._fetch_fund(fund, [(2026, 6)], fast=False, force=False)
    assert result.skipped == 1
    fetch.assert_not_called()


@pytest.mark.parametrize('today,expected', [
    (date(2026, 9, 25), [(2026, 3), (2026, 6)]),
    (date(2026, 9, 30), [(2026, 6), (2026, 9)]),
    (date(2026, 10, 1), [(2026, 6), (2026, 9)]),
    (date(2027, 1, 5), [(2026, 9), (2026, 12)]),
])
def test_incremental_window_revisits_closed_quarters(today, expected):
    assert list(downloader._iter_quarters(downloader._incremental_start(today), today)) == expected


def test_quarter_iterator_never_fetches_future_quarter_end():
    assert list(downloader._iter_quarters(date(2026, 6, 1), date(2026, 9, 25))) == [(2026, 6)]
    assert list(downloader._iter_quarters(date(2026, 6, 1), date(2026, 9, 30))) == [(2026, 6), (2026, 9)]
