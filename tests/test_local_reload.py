from datetime import date,timedelta
from unittest.mock import Mock

from requests.exceptions import ChunkedEncodingError

from scripts.reload_local_data import fm_nav_backfill
from src.base import DownloadResult
from src.etl.mutualFunds.downloaders import cartolaDownloader


def test_monthly_reload_splits_interrupted_ranges_without_losing_days(monkeypatch):
    loaded=[]
    def download(ranges):
        start,end=ranges[0]
        if (end-start).days>7:
            raise ChunkedEncodingError('interrupted CMF transfer')
        loaded.append((start,end))
        return DownloadResult(downloaded=1,rows_upserted=(end-start).days+1)
    instance=Mock();instance._download_ranges.side_effect=download
    monkeypatch.setattr(cartolaDownloader,'CartolaDownloader',lambda **_:instance)
    start,end=date(2026,7,1),date(2026,8,3)
    result=fm_nav_backfill(start,end)
    days=[a+timedelta(days=i) for a,b in loaded for i in range((b-a).days+1)]
    assert days==[start+timedelta(days=i) for i in range((end-start).days+1)]
    assert result.rows_upserted==34
    assert all(a.month==b.month for a,b in loaded)


def test_reload_refuses_remote_database_before_any_query(monkeypatch):
    from types import SimpleNamespace
    from scripts import reload_local_data as reload
    monkeypatch.setattr(reload,'engine',SimpleNamespace(url=SimpleNamespace(host='production.example')))
    monkeypatch.setattr(reload,'snapshot',lambda:(_ for _ in ()).throw(AssertionError('must not query')))
    monkeypatch.setattr('sys.argv',['reload_local_data.py','--since','2026-07-01'])
    import pytest
    with pytest.raises(SystemExit) as exc:
        reload.main()
    assert exc.value.code==2
