from __future__ import annotations

from collections import Counter
from copy import copy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata

from bs4 import BeautifulSoup
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import SessionLocal
from src.db.models.insiders import InsiderCode, InsiderImport, InsiderTransaction
from .codes import CODES, SOURCE_URL


class SourceFormatError(ValueError):
    pass


def clean(value: str) -> str:
    return ' '.join(value.split())


def normalized(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', clean(value).lower()) if not unicodedata.combining(c))


def number(value: str) -> Decimal | None:
    if not value or value.upper() in ('NA', 'N/A', '-'):
        return None
    # The CMF table uses a dot decimal separator and no thousands separator.
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', value):
        raise SourceFormatError(f'Unexpected CMF numeric value: {value!r}')
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise InvalidOperation
        return result
    except InvalidOperation as exc:
        raise SourceFormatError(f'Invalid number: {value!r}') from exc


def flag(value: str) -> bool | None:
    if not value:
        return None
    value = normalized(value)
    if value in ('s', 'si'):
        return True
    if value in ('n', 'no'):
        return False
    raise SourceFormatError(f'Unknown boolean: {value!r}')


def day(value: str, required=False) -> date | None:
    if not value and not required:
        return None
    # CMF also renders these calendar dates with a time component. Parse the
    # complete value so malformed suffixes still fail; raw_cells retains it.
    for fmt in ('%d/%m/%Y', '%d/%m/%Y %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise SourceFormatError(f'Invalid date: {value!r}')


def parse_html(html: str, status: str, start: date, end: date) -> list[dict]:
    if status not in ('VI', 'NV') or start > end:
        raise ValueError('Invalid status or date window')
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.find(id='cuadro')
    # Check the result container AND its following marker; reject truncated/403 pages.
    if container is None or soup.find(id='mod_bitacora') is None:
        raise SourceFormatError('CMF result structure missing or incomplete')
    tables = container.find_all('table')
    if normalized(container.get_text(' ', strip=True)) == 'sin informacion':
        return []
    records = []
    occurrences: Counter = Counter()
    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 4:
            raise SourceFormatError('Incomplete transaction table')
        title_cells = rows[0].find_all('td', recursive=False)
        if len(title_cells) != 1 or title_cells[0].get('colspan') != '29':
            raise SourceFormatError('Unexpected company heading')
        company = clean(title_cells[0].get_text(' ', strip=True))
        headings = [normalized(c.get_text(' ', strip=True)) for c in rows[2].find_all('th')]
        if (len(headings) != 28 or headings[0] != 'rut sociedad'
                or headings[7] != 'fecha transaccion' or 'unidades' not in headings[19]
                or 'pesos' not in headings[21] or 'observaciones' not in headings[27]
                or 'modifica' not in normalized(rows[1].get_text(' ', strip=True))):
            raise SourceFormatError('CMF column layout changed')
        for row in rows[3:]:
            # Some CMF observations contain malformed HTML that nests the last
            # cell inside the observations cell. Count all row cells, then
            # exclude nested cell text from its parent to avoid mixing fields.
            cells = row.find_all('td')
            if len(cells) != 29:
                raise SourceFormatError(f'Expected 29 fields, received {len(cells)}')
            v = []
            for cell in cells:
                isolated = copy(cell)
                for nested in isolated.find_all('td'):
                    nested.decompose()
                v.append(clean(isolated.get_text(' ', strip=True)))
            transaction_date = day(v[7], required=True)
            if not start <= transaction_date <= end:
                raise SourceFormatError(f'Transaction outside requested window: {transaction_date}')
            if not v[0].isdigit() or not v[1] or not v[9] or not v[11]:
                raise SourceFormatError('Missing required company or transaction codes')
            link = cells[28].find('a')
            match = re.search(r'bitacora\(\s*(\d+)\s*,\s*(\d+)\s*\)', link.get('onclick', '')) if link else None
            amendment = flag(v[28])
            if amendment is None:
                raise SourceFormatError('Missing amendment flag')
            record = dict(
                company_rut=v[0], company_name=company, entity_status=status,
                relationship_code=v[1].upper(), informant_rut=v[2] or None,
                informant_dv=v[3].upper() or None, paternal_name=v[4] or None,
                maternal_name=v[5] or None, informant_name=v[6] or None,
                transaction_date=transaction_date, communication_date=day(v[8]),
                transaction_type=v[9].upper(), other_transaction_type=v[10] or None,
                security_type=v[11].upper(), other_security_type=v[12] or None,
                own_company_shares=flag(v[13]), ticker=v[14].upper() or None,
                instrument_issuer_rut=v[15] or None, foreign_issuer=flag(v[16]),
                instrument_issuer_name=v[17] or None, series=v[18] or None,
                units=number(v[19]), unit_price_or_rate=number(v[20]), amount_clp=number(v[21]),
                on_exchange=flag(v[22]), broker_rut=v[23] or None, control_intent=flag(v[24]),
                operation_pct=number(v[25]), final_holding_pct=number(v[26]),
                observations=v[27] or None, is_amendment=amendment,
                amendment_reference=match.group(1) if match else None, raw_cells=v,
            )
            # Status/name can change without creating another disclosure. Preserve
            # source row multiplicity, not just distinct numeric trade details.
            payload = {k: val for k, val in record.items() if k not in ('entity_status', 'company_name', 'raw_cells')}
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
            occurrences[fingerprint] += 1
            record.update(fingerprint=fingerprint, occurrence=occurrences[fingerprint])
            records.append(record)
    if not records:
        raise SourceFormatError('No rows and no explicit SIN INFORMACION result')
    return records


def load_window(start: date, end: date, records: list[dict], sources: list[dict]) -> int:
    """Commit BOTH entity lists as one snapshot; retain superseded rows for audit."""
    if start > end or {s['status'] for s in sources} != {'VI', 'NV'} or len(sources) != 2:
        raise ValueError('A complete VI/NV window is required')
    for source in sources:
        matching = [r for r in records if r['entity_status'] == source['status']]
        if len(matching) != source['row_count']:
            raise ValueError('Source row count mismatch')
    if any(not start <= r['transaction_date'] <= end for r in records):
        raise ValueError('Refusing to load records outside the requested window')
    # CMF can expose the same disclosure under a current and a former issuer
    # name (e.g. Camanchaca). Merge only identical full rows + occurrence,
    # retain both labels, and prefer the VI name for display when available.
    merged = {}
    for record in records:
        key = (record['fingerprint'], record['occurrence'])
        entity = {'status': record['entity_status'], 'name': record['company_name']}
        if key not in merged:
            merged[key] = dict(record, source_entities=[entity])
        else:
            entities = merged[key]['source_entities']
            if entity not in entities:
                entities.append(entity)
            if record['entity_status'] == 'VI':
                merged[key] = dict(record, source_entities=entities)
    records = list(merged.values())
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(12018045)'))
        # Do not let a slower, older fetch overwrite a newer committed snapshot.
        fetched_at = min(datetime.fromisoformat(s['fetched_at']) for s in sources)
        newer = session.execute(text('''SELECT 1 FROM insider_imports
            WHERE date_from<=:end AND date_to>=:start AND fetched_at>:fetched LIMIT 1'''),
            dict(start=start, end=end, fetched=fetched_at)).scalar()
        if newer:
            raise ValueError('Newer snapshot already loaded; refetch this window')
        batch_id = session.execute(insert(InsiderImport).values(
            date_from=start, date_to=end, row_count=len(records), sources=sources,
            fetched_at=fetched_at).returning(InsiderImport.id)).scalar_one()
        session.execute(update(InsiderTransaction).where(
            InsiderTransaction.transaction_date.between(start, end),
            InsiderTransaction.is_current.is_(True)).values(is_current=False))
        for offset in range(0, len(records), 500):
            chunk = [dict(r, is_current=True, last_seen_at=now, last_import_id=batch_id) for r in records[offset:offset+500]]
            stmt = insert(InsiderTransaction).values(chunk)
            stmt = stmt.on_conflict_do_update(index_elements=['fingerprint', 'occurrence'], set_={
                key: getattr(stmt.excluded, key) for key in ('company_name', 'entity_status', 'source_entities', 'raw_cells', 'is_current', 'last_seen_at', 'last_import_id')})
            session.execute(stmt)
        for domain, codes in CODES.items():
            stmt = insert(InsiderCode).values([dict(domain=domain,code=code,label=label,source_url=SOURCE_URL) for code,label in codes.items()])
            session.execute(stmt.on_conflict_do_update(index_elements=['domain','code'],set_={'label':stmt.excluded.label,'source_url':stmt.excluded.source_url}))
        session.commit()
    return len(records)
