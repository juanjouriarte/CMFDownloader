from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from main import health, readiness


def test_liveness_does_not_depend_on_database():
    assert health() == {"status": "ok"}


def test_readiness_reports_healthy_database():
    connection = MagicMock()

    @contextmanager
    def connected():
        yield connection

    with patch("main.engine.connect", side_effect=connected):
        assert readiness() == {"status": "ready", "database": "ok"}

    connection.execute.assert_called_once()


def test_readiness_returns_503_without_exposing_connection_details():
    error = OperationalError("SELECT 1", {}, Exception("secret connection detail"))
    with patch("main.engine.connect", side_effect=error):
        response = readiness()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert b"secret connection detail" not in response.body
    assert response.body == b'{"status":"unavailable","database":"unavailable"}'
