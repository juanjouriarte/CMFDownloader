"""Import actual IFRS net equity; portfolio weights refer to assets, not equity."""
import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import logging
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from sqlalchemy import text
from src.base import DownloadResult
from src.db.engine import SessionLocal
from src.http import make_session, fetch

BASE = 'https://www.cmfchile.cl/institucional/mercados/entidad.php'
LOGGER = logging.getLogger(__name__)
SCALE = Decimal(1000)  # CMF FI IFRS financial statements: thousands of functional currency.
CURRENCIES = {'PROM':'USD', 'US$':'USD', 'USD':'USD', '$':'CLP', '$$':'CLP', 'CLP':'CLP',
              'EURO':'EUR', 'EUR':'EUR', 'COP':'COP', 'PEN':'PEN', 'UF':'UF'}
ELIGIBLE_SQL = '''SELECT f.run_fondo,f.rescatable FROM fondos_inversion f
 WHERE f.vigente AND f.rescatable IS FALSE
 AND NOT EXISTS(SELECT 1 FROM valores_cuota_fi v WHERE v.run_fondo=f.run_fondo
   AND v.fecha<=:today AND v.patrimonio_neto>=0
   AND v.patrimonio_neto::text NOT IN ('NaN','Infinity','-Infinity'))
 ORDER BY f.run_fondo'''


def eligible_funds(today):
    with SessionLocal() as session:
        return [dict(r) for r in session.execute(text(ELIGIBLE_SQL),dict(today=today)).mappings()]


class InvalidReport(ValueError):
    pass


class UnrecognizedReportPage(InvalidReport):
    pass


class XMLDownloadUnavailable(InvalidReport):
    pass


def validate_balances(assets, liabilities, equity):
    if assets < 0 or liabilities < 0 or abs(assets-liabilities-equity) > 3*SCALE:
        raise InvalidReport('Assets, liabilities and net equity do not reconcile')


def parse_report(content: bytes, run: str, period: date) -> dict:
    # No entities/DTDs; only the exact CMF schema and requested reporting context.
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise InvalidReport('XML declarations not allowed')
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        # Some legacy CMF exports contain Latin-1 Spanish text without any
        # encoding declaration. Decode that specific case; never override a
        # declared encoding, repair XML structure, or alter numeric/date fields.
        if re.search(br'<\?xml\b[^?]*\bencoding\s*=',content[:300],re.I):
            raise InvalidReport('Invalid financial-statement XML') from exc
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                root = ET.fromstring(content.decode('iso-8859-1'))
            except ET.ParseError as legacy_exc:
                raise InvalidReport('Invalid financial-statement XML') from legacy_exc
        else:
            raise InvalidReport('Invalid financial-statement XML') from exc
    def field(path):
        nodes = root.findall(path)
        if len(nodes) != 1 or not nodes[0].text:
            raise InvalidReport('Missing or ambiguous report identity')
        return nodes[0].text.strip()
    if root.tag != 'IFRS' or field('Identificacion/RUTFondoInforma') != run:
        raise InvalidReport('Fund identity mismatch')
    if (field('Contextos/PeriodoActual/FechaTermino') != period.isoformat()
        or field('DatosPeriodo/PeriodoPresentacionEstadosFinancieros/Anio') != str(period.year)
        or int(field('DatosPeriodo/PeriodoPresentacionEstadosFinancieros/Mes')) != period.month):
        raise InvalidReport('Reporting date mismatch')
    source_currency = field('DatosPeriodo/MonedaPresentacionEstadosFinancieros')
    currency = CURRENCIES.get(source_currency)
    if not currency:
        raise InvalidReport('Unknown financial-statement currency: '+source_currency)
    def account(code):
        nodes = root.findall(f"Cuenta[@CodigoCuenta='{code}'][@Context='PeriodoActual']")
        if len(nodes) != 1:
            raise InvalidReport('Missing or duplicate account: '+code)
        raw = (nodes[0].text or '').strip()
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',raw):
            raise InvalidReport('Invalid account value: '+code)
        try:
            value = Decimal(raw)*SCALE
        except InvalidOperation as exc:
            raise InvalidReport('Invalid decimal') from exc
        if not value.is_finite():
            raise InvalidReport('Non-finite value')
        return value
    equity = account('TotalPatrimonioNeto')
    assets = account('TotalActivo')
    liabilities = account('TotalPasivoCorriente')+account('TotalPasivoNoCorriente')
    validate_balances(assets,liabilities,equity)
    return dict(run_fondo=run,periodo=period,currency=currency,equity=equity,assets=assets,
                liabilities=liabilities,source_currency=source_currency,source_scale=1000,
                source_sha256=hashlib.sha256(content).hexdigest())


def parse_html_report(content: bytes, run: str, period: date) -> dict:
    """CMF's displayed balance sheet, only when its XML download is unavailable.

    Independently verifies the visible fund RUT, current-column date, explicit
    currency/scale and balance-sheet reconciliation. Never use a comparative
    column or an ambiguous/missing cell, and never bypass invalid XML identities.
    """
    soup = BeautifulSoup(content,'html.parser')
    identity = soup.select('#datos_ent')
    if len(identity)!=1:raise InvalidReport('Missing or ambiguous HTML fund identity')
    ruts=[dt.find_next_sibling('dd').get_text(' ',strip=True) for dt in identity[0].find_all('dt')
          if dt.get_text(strip=True)=='RUT:' and dt.find_next_sibling('dd')]
    if len(ruts)!=1 or not re.fullmatch(r'[\d.]+-[\dkK]',ruts[0]) or ruts[0].split('-')[0].replace('.','')!=run:
        raise InvalidReport('HTML fund identity mismatch')
    headers=[th for th in soup.find_all('th') if 'ESTADO DE SITUACION FINANCIERA' in th.get_text()]
    if len(headers)!=1:raise InvalidReport('Missing or ambiguous HTML balance sheet')
    header=headers[0];table=header.find_parent('table')
    unit=re.search(r'Expresado en miles de\s+([^"()]+)',header.get_text(' ',strip=True))
    labels={'Pesos':'CLP','Dolar':'USD','Dólar':'USD','Dolares':'USD','Dólares':'USD',
            'Euro':'EUR','Euros':'EUR','Pesos Colombianos':'COP','Soles':'PEN','UF':'UF'}
    source_currency=unit.group(1).strip() if unit else ''
    currency=labels.get(source_currency)
    if not currency:raise InvalidReport('Unknown HTML currency or unconfirmed source scale')
    def cells(row):
        result=[]
        for cell in row.find_all(['th','td'],recursive=False):
            width=int(cell.get('colspan',1))
            if width<1 or width>5:raise InvalidReport('Unexpected HTML column structure')
            result.extend([' '.join(cell.get_text(' ',strip=True).split())]*width)
        return result
    heading=cells(header.find_parent('tr'))
    if len(heading)==5 and heading[3:]==['PERIODOS','PERIODOS'] and header.get('rowspan')=='2':
        dates_row=header.find_parent('tr').find_next_sibling('tr')
        date_cells=cells(dates_row) if dates_row else []
        if len(date_cells)!=2:raise InvalidReport('Unexpected HTML date header structure')
        heading=heading[:3]+date_cells
    if len(heading)!=5 or heading[3]!='Al '+period.strftime('%d/%m/%Y'):
        raise InvalidReport('HTML reporting date mismatch')
    balances={}
    required={'Total Activo','Total Pasivo Corriente','Total Pasivo No Corriente','Total Patrimonio Neto','Total Pasivo'}
    for tr in table.find_all('tr'):
        # Notes/separator rows sometimes span more than the five data columns.
        # Account rows and the dated heading still require the exact structure.
        if not any(' '.join(cell.get_text(' ',strip=True).split()).split('(')[0].strip() in required
                   for cell in tr.find_all('td',recursive=False)):
            continue
        values=cells(tr)
        if len(values)!=5:continue
        name=values[1].split('(')[0].strip()
        if name not in required:continue
        if name in balances:raise InvalidReport('Duplicate HTML account: '+name)
        raw=values[3]
        if not re.fullmatch(r'-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?',raw):
            raise InvalidReport('Invalid HTML account value: '+name)
        balances[name]=Decimal(raw.replace('.','').replace(',','.'))*SCALE
    if set(balances)!=required:raise InvalidReport('Missing HTML balance-sheet account')
    assets=balances['Total Activo'];equity=balances['Total Patrimonio Neto']
    liabilities=balances['Total Pasivo Corriente']+balances['Total Pasivo No Corriente']
    validate_balances(assets,liabilities,equity)
    if abs(assets-balances['Total Pasivo'])>3*SCALE:
        raise InvalidReport('HTML balance-sheet total does not reconcile')
    return dict(run_fondo=run,periodo=period,currency=currency,equity=equity,assets=assets,
                liabilities=liabilities,source_currency=source_currency,source_scale=1000,
                source_sha256=hashlib.sha256(content).hexdigest())


def report_link(html: str, run: str, period: date) -> str | None:
    soup = BeautifulSoup(html,'html.parser')
    links = []
    for a in soup.find_all('a',href=True):
        url = urljoin(BASE,a['href']); parsed = urlparse(url); query = parse_qs(parsed.query)
        if 'Estados_financieros' not in query.get('desc_archivo',[''])[0]:
            continue
        if (parsed.scheme != 'https' or parsed.netloc != 'www.cmfchile.cl'
            or parsed.path != '/institucional/inc/inf_financiera/ifrs_xml/ifrs_xml_verarchivo.php'
            or query.get('rut') != [run] or query.get('periodo') != [period.strftime('%Y%m')]
            or not query.get('archivo',[''])[0].lower().endswith('.xml')):
            raise InvalidReport('Financial-statement link identity mismatch')
        links.append(url)
    if not links:
        if ('No existe información' in soup.get_text(' ',strip=True)
            or soup.find(string=lambda value: value and value.strip()=='Sin información.')):
            return None
        raise UnrecognizedReportPage('Financial-statement response has no XML link')
    if len(set(links)) != 1 or 'Expresado en miles de' not in soup.get_text(' ',strip=True):
        raise InvalidReport('Unconfirmed source scale or ambiguous report')
    return links[0]


def save_report(row):
    with SessionLocal() as s:
        s.execute(text('''INSERT INTO fi_reported_equity
          (run_fondo,periodo,currency,equity,assets,liabilities,source_currency,source_scale,source_url,source_sha256)
          VALUES (:run_fondo,:periodo,:currency,:equity,:assets,:liabilities,:source_currency,:source_scale,:source_url,:source_sha256)
          ON CONFLICT(run_fondo,periodo) DO UPDATE SET currency=EXCLUDED.currency,
            equity=EXCLUDED.equity,assets=EXCLUDED.assets,liabilities=EXCLUDED.liabilities,
            source_currency=EXCLUDED.source_currency,source_scale=EXCLUDED.source_scale,
            source_url=EXCLUDED.source_url,source_sha256=EXCLUDED.source_sha256,fetched_at=now()'''),row)
        s.commit()


def fetch_report(http, link, run, period, referer):
    # CMF sometimes returns a temporary access-block message with HTTP 200.
    # Retry that response only; never loosen financial/identity validation.
    for attempt in range(3):
        raw=fetch(http,link,timeout=40,headers={'Referer':referer}).content
        blocked=b'ACCION NO PERMITIDA' in raw[:500].upper() or b'403 FORBIDDEN' in raw[:500].upper()
        if not blocked:
            return parse_report(raw,run,period)
        if attempt<2:time.sleep(attempt+1)
    raise XMLDownloadUnavailable('CMF blocked the XML download after 3 attempts')


def fetch_statement(http, page, link, run, period):
    try:
        row=fetch_report(http,link,run,period,page.url)
        row['source_url']=link
    except XMLDownloadUnavailable:
        row=parse_html_report(page.content,run,period)
        row['source_url']=BASE+'?'+urlencode(dict(mercado='V',rut=run,tipoentidad='FINRE',
            vig='VI',control='svs',pestania='29',mm=f'{period.month:02d}',aa=period.year))
        LOGGER.info('FI equity %s %s: validated public HTML statement; XML unavailable',run,period)
    return row


def fetch_report_page(http, run, period):
    for attempt in range(3):
        page=fetch(http,BASE,method='POST',params=dict(mercado='V',rut=run,
            tipoentidad='FINRE',vig='VI',control='svs',pestania='29'),
            data=dict(mm=f'{period.month:02d}',aa=str(period.year),rut=run),timeout=40)
        try:
            return page,report_link(page.text,run,period)
        except UnrecognizedReportPage:
            if attempt==2:raise
            time.sleep(attempt+1)


def recent_quarters(today):
    current = today.year*4+(today.month-1)//3
    # A quarter becomes eligible the day after closing.
    result = []
    for index in (current-1,current-2):
        year,q = divmod(index,4);month=(q+1)*3
        result.append(date(year,month,calendar.monthrange(year,month)[1]))
    return result


def quarter_range(start, end, today=None):
    """Actual quarter ends in the requested interval, excluding open quarters."""
    today = today or date.today()
    if start > end:
        raise ValueError('Start date must not exceed end date')
    periods = []
    for year in range(start.year, min(end, today).year + 1):
        for month in (3, 6, 9, 12):
            period = date(year, month, calendar.monthrange(year, month)[1])
            if start <= period <= end and period < today:
                periods.append(period)
    return periods


class ReportedEquityDownloader:
    def run(self, runs: list[str] | None = None, *, from_date=None, to_date=None,
            skip_existing=False, completed_missing=None, on_result=None, workers=2):
        today=date.today()
        if not 1 <= workers <= 4:
            raise ValueError('Use between one and four workers')
        if to_date is not None and from_date is None:
            raise ValueError('A start date is required for a historical interval')
        periods = quarter_range(from_date,to_date or today,today) if from_date else recent_quarters(today)
        funds = eligible_funds(today)
        if runs is not None:
            funds=[f for f in funds if f['run_fondo'] in set(runs)]
        LOGGER.info('FI equity: %d active non-rescatable funds without daily NAV',len(funds))
        LOGGER.info('FI equity: %d quarters, %d fund-period checks',len(periods),len(funds)*len(periods))
        existing = set()
        if skip_existing and periods:
            with SessionLocal() as session:
                existing = set(session.execute(text('''SELECT run_fondo,periodo FROM fi_reported_equity
                    WHERE periodo BETWEEN :start AND :end'''),dict(start=min(periods),end=max(periods))).all())
        completed_missing = completed_missing or set()
        def import_fund(f):
            result=DownloadResult();run=f['run_fondo']
            with make_session() as http:
                for period in periods:
                    outcome=dict(run=run,period=period.isoformat())
                    if (run,period) in existing or (run,period) in completed_missing:
                        result.skipped+=1
                        outcome['status']='existing' if (run,period) in existing else 'resumed_missing'
                        if on_result:on_result(outcome)
                        continue
                    try:
                        page,link=fetch_report_page(http,run,period)
                        if link is None:
                            result.skipped+=1
                            outcome['status']='unavailable'
                        else:
                            row=fetch_statement(http,page,link,run,period)
                            save_report(row);result.downloaded+=1;result.rows_upserted+=1
                            outcome.update(status='saved',currency=row['currency'],equity=str(row['equity']),
                                           source_format='html' if '/entidad.php?' in row['source_url'] else 'xml')
                    except Exception as exc:
                        LOGGER.warning('FI equity %s %s: %s',run,period,str(exc)[:250])
                        result.errors+=1
                        outcome.update(status='error',error=str(exc)[:250])
                    finally:
                        time.sleep(.25)
                    if on_result:on_result(outcome)
            LOGGER.info('FI equity %s: %s',run,result)
            return result
        result=DownloadResult()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for part in pool.map(import_fund,funds):result+=part
        return result
