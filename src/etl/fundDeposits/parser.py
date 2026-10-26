"""Parse the full CMF registry; a deposit is not an operations start date."""
from datetime import datetime
import re
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup, SoupStrainer

SOURCE_URL = 'https://www.cmfchile.cl/institucional/inc/deposito_fondos_mutuos.php'
FUND_TYPES = {'FM': 'Fondo Mutuo', 'FIR': 'Fondo Inversión Rescatable', 'FNR': 'Fondo Inversión No Rescatable'}
HEADERS = ['N° de Registro', 'Fecha de Depósito', 'RUN del Fondo', 'Nombre del Fondo',
    'Nombre Administradora', 'Fecha Última modificación', 'Estado (Vigente/No Vigente)',
    'Estado (indica si fondo está liquidado)', 'Reglamento Interno Vigente',
    'Reglamento Interno en Modificación', 'Resumen Modificación Reglamento Interno',
    'Contrato General del Fondo Vigente', 'Contrato General del Fondo en Modificación',
    'Resumen Modificación Contrato General', 'Reglamento General del Fondo Vigente',
    'Reglamento General del Fondo en Modificación', 'Resumen Modificación Reglamento General',
    'Tipo Fondo', 'Resolución de Aprobación']
DOCUMENT_KEYS = ['regulation', 'regulation_pending', 'regulation_summary', 'contract',
    'contract_pending', 'contract_summary', 'general_regulation', 'general_regulation_pending',
    'general_regulation_summary']


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c)).lower().strip()


def document(cell, kind, label):
    links = [a for a in cell.find_all('a') if a.get_text(strip=True)]
    if not links:
        if cell.get_text(strip=True):
            return {'kind':kind,'label':label,'reference':cell.get_text(' ',strip=True),
                    'url':None,'source_url':None}
        return None
    if len(links) != 1:
        raise ValueError(f'Ambiguous document: {kind}')
    a = links[0]
    source = a.get('href', '')
    if source.startswith('javascript:') or not source:
        match = re.fullmatch(r"\s*(?:verArchivo|ventana_url)\(\s*(['\"])(.*?)\1\s*\);?\s*", a.get('onclick', ''))
        if not match:
            raise ValueError('Unrecognized document link')
        source = match[2]
    source = urljoin(SOURCE_URL, source)
    u = urlsplit(source)
    if (u.scheme not in ('http', 'https') or u.hostname not in
        ('www.cmfchile.cl', 'cmfchile.cl', 'www.svs.cl', 'svs.cl') or u.username or u.password
        or not u.path.startswith('/documentos/') or not u.path.lower().endswith('.pdf')):
        raise ValueError('Unexpected CMF document URL')
    # CMF serves the same official document paths formerly hosted by SVS.
    url = urlunsplit(('https', 'www.cmfchile.cl', u.path, u.query, ''))
    return {'kind': kind, 'label': label, 'reference': a.get_text(' ', strip=True),
            'url': url, 'source_url': source}


def parse_html(html):
    if '</html>' not in html.lower() or html.lower().count('<table') != html.lower().count('</table>'):
        raise ValueError('Incomplete CMF response')
    soup = BeautifulSoup(html, 'html.parser', parse_only=SoupStrainer('div', id='tabla_resultado'))
    tables = soup.find_all('table')
    if not tables:
        raise ValueError('CMF registry table not found')
    snapshots = []
    for table in tables:
        rows = table.find_all('tr')
        headers = [c.get_text(' ', strip=True) for c in rows[0].find_all('th')]
        if [normalized(h) for h in headers] != [normalized(h) for h in HEADERS]:
            raise ValueError('CMF registry columns changed')
        records = {}
        for row in rows[1:]:
            cells = row.find_all('td', recursive=False)
            if len(cells) != len(HEADERS):
                raise ValueError('Incomplete CMF registry row')
            values = [' '.join(c.get_text(' ', strip=True).split()) for c in cells]
            registration, _, run, name, admin = values[:5]
            if not re.fullmatch(r'[A-Z0-9]+', registration) or not re.fullmatch(r'\d+', run) or not admin:
                raise ValueError('Invalid registry identity')
            type_code = next((k for k, v in FUND_TYPES.items() if normalized(v) == normalized(values[17])), None)
            if not type_code or values[6] not in ('Vigente', 'No Vigente') or values[7] not in ('', 'Liquidado'):
                raise ValueError('Unknown fund type or status')
            docs = [document(cells[i], key, HEADERS[i]) for i, key in enumerate(DOCUMENT_KEYS, 8)]
            docs.append(document(cells[18], 'approval', HEADERS[18]))
            record = dict(registration=registration, run=run, fund_name=name, administrator=admin,
                deposit_date=datetime.strptime(values[1], '%d/%m/%Y').date(),
                modified_date=datetime.strptime(values[5], '%d/%m/%Y').date(), fund_type=type_code,
                active=values[6] == 'Vigente', liquidated=values[7] == 'Liquidado',
                documents=[d for d in docs if d], approval_reference=values[18] or None)
            key = (registration, run)  # CMF reuses at least one registration across different RUNs.
            if key in records:
                raise ValueError('Duplicate CMF registry identity')
            records[key] = record
        if not records:
            raise ValueError('Empty registry snapshot; previous data preserved')
        snapshots.append(records)
    # CMF renders the same registry twice (normal and enlarged). Never double-count it.
    if any(s != snapshots[0] for s in snapshots[1:]):
        raise ValueError('CMF mirrored tables disagree')
    return list(snapshots[0].values())
