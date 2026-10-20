"""Reported debt terms; never interpret price/par as yield or mix rate bases.

CMF Circular 1333, sections 6.01/6.02.11; Circular 1999, Annex 3,
sections 12-15: valuation code 1=TIR, 2=par, 3=price/relevant value.
https://www.cmfchile.cl/normativa/cir_1999_2010.pdf (pp. 32-33)
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from math import isfinite

from .currency_exposure import normalize

DEBT_TYPES = set('''1460 19980 AFR AFROIND ATP BA BB BBFE BBNEE BBP BBPNEE BC BCD BCP BCU BCX
BE BEBCE BEC BEE BEEC BEEPOIED BH BN BNE BNEE BR BS BSP BTF BTP BTU BU BVL CACR
CERO CORA CS CSOIND DPBFE DPC DPL EC ECN ECNE FACT LBFE LH MH MHA MHB NEERF NEERFOIED
OBF OBFE OEE OTDN OTDNE OTROD OTROF PARC PCC PCD PCNP PCOM PCOMP PCX PDBC PDP PE
PEBCE PEE PEOIND PHIP PHIPP PLC PLCP PNR PPBC PPEN PRBC PRC PRD PRT PRT-P PTF RDN ZERO'''.split())
NON_DEBT_TYPES = set('''ACC ACCOI ACCR ACCROIND ACE ACN ACNE ADR ADRN BPF BPT BRHE BRNHE BZ
CAMS CAOC CAPC CDV CEMS CEOC CJ CLEAS CLEASOIND CT CTE DPFI DPFIE DPSA DPSAE ETFA ETFB
ETFC FC FEX FV FWC FWV IMEQ IMMM IMOT IMVH LA MON NEERV NEERVOIED NEERVOIND
OTROC PAMS PAOC PCCO PCCV PCV PEMS PEOC PSCO PSFI PVCC S SIM'''.split())
INTEREST_TYPES = {'NL':'Nominal lineal', 'NC':'Nominal compuesta', 'RL':'Real lineal', 'RC':'Real compuesta'}
BUCKETS = [('past','Vencido al corte'),('m3','0–3 meses'),('y1','3–12 meses'),
           ('y3','1–3 años'),('y5','3–5 años'),('long','Más de 5 años'),
           ('perpetual','Perpetuo'),('unknown','Sin vencimiento informado')]


def number(value):
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def weight(value):
    value = number(value)
    return value if value is not None and 0 <= value <= 100 else None


def token(value):
    value = str(value or '').strip().upper()
    return '' if value in ('NAN','NONE','NULL','NA','N/A','-') else value


def maturity_date(value):
    if isinstance(value, date):
        value = value.date() if isinstance(value, datetime) else value
        return value if 1900 < value.year < 9999 else None
    value = token(value)
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%Y%m%d'):
        try:
            result = datetime.strptime(value, fmt).date()
            return result if 1900 < result.year < 9999 else None
        except ValueError:
            pass
    return None


def debt_kind(p):
    code = token(p.get('instrument_type'))
    if code in DEBT_TYPES:
        return True
    if code in NON_DEBT_TYPES or code.startswith('CF'):
        return False
    return None


def terms(p):
    code = token(p.get('valuation_code')).lstrip('0')
    base = number(p.get('rate_base'))
    base = int(base) if base is not None and base > 0 and base.is_integer() else None
    interest = token(p.get('interest_type'))
    interest = interest if interest in INTEREST_TYPES else None
    currency = normalize(p.get('quantity_unit'))
    is_debt = debt_kind(p)
    value = number(p.get('reported_yield')) if code == '1' and is_debt is True else None
    maturity = maturity_date(p.get('maturity_date'))
    perpetual = token(p.get('instrument_type')) in ('BBP','BBPNEE')
    return dict(is_debt=is_debt, maturity_date=None if perpetual else maturity,
                maturity_status='perpetual' if perpetual else 'reported' if maturity else 'missing',
                yield_pct=value, base_days=base, interest_type=interest,
                rate_currency=currency, valuation_code=code or None)


def shift_months(day, months):
    n = day.year * 12 + day.month - 1 + months
    year, month = n // 12, n % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def maturity_bucket(t, cutoff):
    if t['maturity_status'] == 'perpetual':
        return 'perpetual'
    maturity = t['maturity_date']
    if maturity is None:
        return 'unknown'
    if maturity < cutoff:
        return 'past'
    for months, key in [(3,'m3'),(12,'y1'),(36,'y3'),(60,'y5')]:
        if maturity <= shift_months(cutoff, months):
            return key
    return 'long'


def rate_key(t):
    if t['yield_pct'] is None or t['base_days'] is None or not t['interest_type'] or t['rate_currency'] == 'UNKNOWN':
        return None
    return f"{t['rate_currency']}:{t['interest_type']}:{t['base_days']}"


def weighted_rates(values):
    total = sum(w for _, w in values)
    return sum(v*w for v,w in values)/total if total > 0 else None


def instrument_terms(records, observed=True):
    if not records:
        return None
    ts = [terms(p) for p in records]
    first = ts[0]
    result = dict(first, yield_status='reported' if first['yield_pct'] is not None else 'unavailable')
    maturities = {t['maturity_date'] for t in ts}
    states = {t['maturity_status'] for t in ts}
    result['maturity_date'] = first['maturity_date'] if len(maturities) == 1 else None
    if len(maturities) > 1 or len(states) > 1:
        result['maturity_status'] = 'mixed'
    known = sorted(d for d in maturities if d)
    result['maturity_from'] = known[0] if known else None
    result['maturity_to'] = known[-1] if known else None
    conventions = {(t['base_days'],t['interest_type'],t['rate_currency']) for t in ts}
    yields = [t['yield_pct'] for t in ts]
    if len(conventions) > 1:
        result.update(yield_pct=None, base_days=None, interest_type=None, rate_currency='UNKNOWN', yield_status='mixed')
    elif any(v is None for v in yields):
        result.update(yield_pct=None, yield_status='unavailable')
    elif len(set(yields)) == 1:
        result['yield_pct'] = yields[0]
    elif rate_key(first) and all(weight(p['weight']) is not None for p in records):
        result['yield_pct'] = weighted_rates([(v,weight(p['weight'])) for v,p in zip(yields,records)])
        result['yield_status'] = 'weighted' if result['yield_pct'] is not None else 'unavailable'
    else:
        result.update(yield_pct=None, yield_status='mixed')
    if not observed:
        result.update(maturity_date=None, maturity_status='partial', yield_pct=None, yield_status='partial')
    return result


def profile(records, cutoff):
    if not records:
        return None
    buckets = {key:dict(key=key,label=label,weight=0.,positions=0) for key,label in BUCKETS}
    debt_weight = non_debt_weight = unclassified_weight = maturity_weight = years_sum = yield_weight = 0.
    debt_positions = invalid_weights = 0
    groups = {}
    for p in records:
        t = terms(p)
        w = weight(p['weight'])
        if w is None:
            invalid_weights += 1
        if t['is_debt'] is not True:
            if t['is_debt'] is False:
                non_debt_weight += w or 0.
            else:
                unclassified_weight += w or 0.
            continue
        debt_positions += 1
        debt_weight += w or 0.
        bucket = maturity_bucket(t, cutoff)
        buckets[bucket]['positions'] += 1
        buckets[bucket]['weight'] += w or 0.
        if w is not None and t['maturity_date'] and t['maturity_date'] >= cutoff:
            maturity_weight += w
            years_sum += w * (t['maturity_date']-cutoff).days / 365.25
        key = rate_key(t)
        if not key or w is None or w <= 0:
            continue
        group = groups.setdefault(key,dict(key=key,currency=t['rate_currency'],interest_type=t['interest_type'],
            base_days=t['base_days'],weight=0.,positions=0,values=[],buckets=defaultdict(list)))
        group['weight'] += w
        group['positions'] += 1
        group['values'].append((t['yield_pct'],w))
        group['buckets'][bucket].append((t['yield_pct'],w))
        yield_weight += w
    for g in groups.values():
        values = g.pop('values')
        g.update(mean=weighted_rates(values),minimum=min(v for v,_ in values),maximum=max(v for v,_ in values))
        g['buckets'] = [dict(key=key,label=label,mean=weighted_rates(g['buckets'][key]),
                            weight=sum(w for _,w in g['buckets'][key]),positions=len(g['buckets'][key])) for key,label in BUCKETS]
    return dict(period=cutoff,debt_weight=debt_weight,debt_positions=debt_positions,
                non_debt_weight=non_debt_weight,unclassified_weight=unclassified_weight,invalid_weights=invalid_weights,
                maturity_weight=maturity_weight,average_maturity_years=years_sum/maturity_weight if maturity_weight else None,
                maturity_buckets=list(buckets.values()),yield_weight=yield_weight,
                yield_groups=sorted(groups.values(),key=lambda g:(-g['weight'],g['key'])))
