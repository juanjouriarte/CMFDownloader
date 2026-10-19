from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from src.etl.investmentFunds.downloaders import valoresCuotaDownloader as m


@pytest.mark.parametrize('today,start', [
    (date(2026,10,13),date(2026,7,13)),
    (date(2027,1,31),date(2026,10,31)),
    (date(2026,5,31),date(2026,2,28)),
    (date(2024,5,31),date(2024,2,29)),
])
def test_three_calendar_month_window(today,start):
    assert m._incremental_start(today)==start


def test_scheduled_run_requests_whole_three_month_window(tmp_path,monkeypatch):
    class Today(date):
        @classmethod
        def today(cls):return cls(2026,10,13)
    monkeypatch.setattr(m,'date',Today)
    monkeypatch.setattr(m,'DOWNLOADS_DIR',tmp_path)
    d=m.ValoresCuotaFIDownloader()
    d._download_all=Mock()
    d.run()
    d._download_all.assert_called_once_with(date(2026,7,13),date(2026,10,13),only_vigentes=True)


@pytest.mark.parametrize('force',[False,True])
def test_recent_nav_does_not_short_circuit_repair(tmp_path,monkeypatch,force):
    monkeypatch.setattr(m,'DOWNLOADS_DIR',tmp_path)
    monkeypatch.setattr(m.time,'sleep',lambda _:None)
    monkeypatch.setattr(m,'load_valores_cuota',Mock(return_value=3))
    monkeypatch.setattr(m,'mark_has_data',Mock())
    d=m.ValoresCuotaFIDownloader(force=force)
    d._get_funds=Mock(return_value=[SimpleNamespace(run_fondo='7010',razon_social='Pionero',rescatable=False,vigente=True)])
    d._last_fecha=Mock(return_value=date(2026,10,13))
    d._fetch=Mock(return_value=[{'fecha':date(2026,7,15)}])
    result=d._download_all(date(2026,7,1),date(2026,10,13),True)
    d._fetch.assert_called_once_with('7010',False,True,date(2026,7,1),date(2026,10,13))
    d._last_fecha.assert_not_called()
    assert result.rows_upserted==3 and result.downloaded==1


def test_empty_recent_window_does_not_disable_inactive_fund(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'DOWNLOADS_DIR',tmp_path)
    monkeypatch.setattr(m.time,'sleep',lambda _:None)
    monkeypatch.setattr(m,'load_valores_cuota',Mock(return_value=0))
    mark=Mock();monkeypatch.setattr(m,'mark_has_data',mark)
    d=m.ValoresCuotaFIDownloader()
    d._get_funds=Mock(return_value=[SimpleNamespace(run_fondo='1',razon_social='Closed Fund',rescatable=False,vigente=False)])
    d._fetch=Mock(return_value=[])
    d._download_all(date(2026,7,1),date(2026,10,13),False)
    mark.assert_not_called()
    d._download_all(m.BACKFILL_START,date(2026,10,13),False)
    mark.assert_called_once_with('1',False)
