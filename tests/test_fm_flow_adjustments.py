from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.etl.mutualFunds.flow_adjustments import run


def _session_with_context(context: dict, rowcount: int = 0):
    session = MagicMock()
    context_result = MagicMock()
    context_result.mappings.return_value.one.return_value = context
    detector_result = MagicMock()
    detector_result.rowcount = rowcount
    session.execute.side_effect = [context_result, detector_result, MagicMock()]
    return session


def test_first_adjustment_scan_backfills_current_year():
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    session = _session_with_context({
        "data_date": date(2026, 9, 14),
        "last_data_date": None,
        "last_full_scan_at": None,
    }, rowcount=6)

    with (
        patch(
            "src.etl.mutualFunds.flow_adjustments.SessionLocal"
        ) as session_factory,
        patch("src.etl.mutualFunds.flow_adjustments.datetime") as clock,
    ):
        session_factory.return_value.__enter__.return_value = session
        clock.now.return_value = now
        result = run()

    detector_params = session.execute.call_args_list[1].args[1]
    state_params = session.execute.call_args_list[2].args[1]
    assert detector_params == {
        "scan_from": date(2026, 1, 1),
        "data_date": date(2026, 9, 14),
    }
    assert state_params["full_scan_at"] == now
    assert result.rows_upserted == 6
    session.commit.assert_called_once_with()


def test_routine_adjustment_scan_uses_incremental_watermark():
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    session = _session_with_context({
        "data_date": date(2026, 9, 15),
        "last_data_date": date(2026, 9, 14),
        "last_full_scan_at": now - timedelta(days=1),
    })

    with (
        patch(
            "src.etl.mutualFunds.flow_adjustments.SessionLocal"
        ) as session_factory,
        patch("src.etl.mutualFunds.flow_adjustments.datetime") as clock,
    ):
        session_factory.return_value.__enter__.return_value = session
        clock.now.return_value = now
        run()

    detector_params = session.execute.call_args_list[1].args[1]
    state_params = session.execute.call_args_list[2].args[1]
    assert detector_params["scan_from"] == date(2026, 9, 7)
    assert state_params["full_scan_at"] is None
