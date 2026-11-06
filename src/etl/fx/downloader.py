"""Daily BDE imports; never emit authenticated URLs or source error bodies."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
import os
import time
from zoneinfo import ZoneInfo
from urllib.parse import quote, quote_plus

from sqlalchemy import text
from src.base import DownloadResult
from src.db.engine import SessionLocal
from src.http import make_session
from .catalog import SERIES

URL = 'https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx'
START = date(2019, 12, 25)  # Prior-business-day seed for January 2020.
logger = logging.getLogger(__name__)


class BDEError(RuntimeError):
    pass


def parse_series(payload, code, start, end):
    if not isinstance(payload, dict) or payload.get('Codigo') != 0:
        raise BDEError('Banco Central rechazó la consulta. Revisa la configuración de acceso.')
    series = payload.get('Series') or {}
    if not isinstance(series,dict) or series.get('seriesId') != code or not isinstance(series.get('Obs'), list):
        raise BDEError('Respuesta BDE incompatible con la serie solicitada.')
    rows = {}
    for item in series['Obs']:
        try:
            day = datetime.strptime(item['indexDateString'], '%d-%m-%Y').date()
            if not start <= day <= end:
                raise ValueError()
            status = item['statusCode']
            if status not in ('OK', 'ND'):
                raise ValueError()
            value = Decimal(str(item['value'])) if status == 'OK' else None
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError()
            row = dict(series_code=code,date=day,value=value,status=status)
            if day in rows and rows[day] != row:
                raise ValueError()
            rows[day] = row
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise BDEError('Observación BDE inválida; no se importó esta serie.') from None
    if not rows:
        raise BDEError('Banco Central devolvió una serie vacía; se conserva el punto de reanudación.')
    return list(rows.values())


def fetch_series(session, token, code, start, end):
    class CredentialFilter(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            return not any(secret in message for secret in (token,quote(token,safe=''),quote_plus(token)))
    guard = CredentialFilter()
    logs = [logging.getLogger(name) for name in ('urllib3.connectionpool','urllib3.util.retry')]
    for log in logs:log.addFilter(guard)
    try:
        return _fetch_series(session,token,code,start,end)
    finally:
        for log in logs:log.removeFilter(guard)


def _fetch_series(session, token, code, start, end):
    # HTTP exceptions can contain query-string credentials: replace them, without chaining.
    payload = None
    for attempt in range(3):
        try:
            response = session.get(URL, params=dict(token=token,function='GetSeries',timeseries=code,
                firstdate=start.isoformat(),lastdate=end.isoformat()), timeout=(10,60),allow_redirects=False)
            if response.status_code == 200:
                payload = response.json()
                break
            if response.status_code not in (429,500,502,503,504):
                break
        except Exception:
            pass  # No exception URLs, bodies or credentials in job history/logs.
        if attempt < 2:time.sleep(2**attempt)
    if payload is None:
        raise BDEError('No se pudo consultar Banco Central. Revisa conexión y acceso a BDE.')
    return parse_series(payload,code,start,end)


def seed_catalog():
    with SessionLocal() as s:
        s.execute(text('''INSERT INTO fx_series(currency,series_code,name,unit_kind)
            VALUES(:currency,:series_code,:name,:unit_kind)
            ON CONFLICT(currency) DO UPDATE SET name=EXCLUDED.name'''),SERIES)
        s.commit()


class FXDownloader:
    def run(self):
        token = os.getenv('BCCH_API_TOKEN','').strip()
        if not token:
            raise BDEError('Configura BCCH_API_TOKEN en el entorno del worker para importar tipos de cambio.')
        seed_catalog()
        today = datetime.now(ZoneInfo('America/Santiago')).date()
        result = DownloadResult()
        with SessionLocal() as s:
            checkpoints = dict(s.execute(text('SELECT currency,checked_through FROM fx_series')).all())
        with make_session() as http:
            # Retry manually: urllib3's automatic retry warnings can include the token URL.
            from urllib3.util.retry import Retry
            for adapter in http.adapters.values():adapter.max_retries = Retry(total=0)
            for spec in SERIES:
                checkpoint = checkpoints.get(spec['currency'])
                start = max(START, checkpoint-timedelta(days=14)) if checkpoint else START
                try:
                    rows = fetch_series(http,token,spec['series_code'],start,today)
                    with SessionLocal() as s:
                        s.execute(text('''INSERT INTO fx_observations(series_code,date,value,status)
                            VALUES(:series_code,:date,:value,:status) ON CONFLICT(series_code,date)
                            DO UPDATE SET value=EXCLUDED.value,status=EXCLUDED.status,fetched_at=now()'''),rows)
                        s.execute(text('''UPDATE fx_series SET checked_through=:day,imported_at=now()
                            WHERE currency=:currency'''),dict(day=max(r['date'] for r in rows),currency=spec['currency']))
                        s.commit()
                    result.downloaded += 1
                    result.rows_upserted += len(rows)
                except BDEError as e:
                    logger.warning('FX %s: %s',spec['currency'],e)
                    result.errors += 1
                time.sleep(1)  # Sequential, modest API load; full history only on first import.
        return result
