from unittest.mock import Mock, patch
import pytest
from src.scheduler import _nav_with_industry_refresh


def test_monthly_refresh_runs_after_nav_and_keeps_import_result():
    events = []
    result = object()
    def download():
        events.append('download')
        return result
    with patch('src.scheduler._run_tracked', side_effect=lambda _, fn: fn()), \
         patch('src.scheduler._refresh_view', side_effect=lambda view: events.append(view)):
        assert _nav_with_industry_refresh(download, 'fm') is result
    assert events == ['download', 'mv_nnm_daily_fm', 'mv_industry_monthly_fm']


def test_failed_import_does_not_refresh_history():
    with patch('src.scheduler._run_tracked') as tracked:
        with pytest.raises(RuntimeError):
            _nav_with_industry_refresh(Mock(side_effect=RuntimeError('import failed')), 'fi')
    tracked.assert_not_called()


def test_failed_daily_refresh_fails_job_before_publishing_monthly_cutoff():
    with patch('src.scheduler._run_tracked', side_effect=lambda _, fn: fn()), \
         patch('src.scheduler._refresh_view', side_effect=RuntimeError('refresh failed')) as refresh:
        with pytest.raises(RuntimeError,match='refresh failed'):
            _nav_with_industry_refresh(Mock(return_value=object()), 'fi')
    refresh.assert_called_once_with('mv_nnm_daily_fi')
