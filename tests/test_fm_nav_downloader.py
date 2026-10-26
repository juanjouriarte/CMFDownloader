from datetime import date
from unittest.mock import Mock
import pytest
from src.etl.mutualFunds.downloaders import cartolaDownloader as m

@pytest.mark.parametrize('last,start',[
    (date(2026,10,25),date(2026,10,12)),
    (date(2026,10,26),date(2026,10,12)),
    (date(2026,9,30),date(2026,9,30)),
    (None,date(2019,12,31)),
])
def test_run_rechecks_late_reports_without_losing_older_catchup(monkeypatch,tmp_path,last,start):
    class Today(date):
        @classmethod
        def today(cls):return cls(2026,10,26)
    monkeypatch.setattr(m,'date',Today)
    d=m.CartolaDownloader(output_dir=tmp_path)
    d._last_date_in_db=Mock(return_value=last)
    d._download_ranges=Mock()
    d.run()
    ranges=d._download_ranges.call_args.args[0]
    assert ranges[0][0]==start
    assert ranges[-1][1]==date(2026,10,26)
    assert all((b[0]-a[1]).days==1 for a,b in zip(ranges,ranges[1:]))
