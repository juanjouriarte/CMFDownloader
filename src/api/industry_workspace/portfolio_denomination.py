"""Denomination from reported quantity units, never settlement currency.

Keep units of funds/ETFs separate: their underlying exposures require look-through.
Return null for incomplete observations, {} only for an observed absence.
"""
from collections import defaultdict

from .currency_exposure import normalize
from .portfolio_debt import token, weight


def denomination_weights(records, observed=True):
    if not observed:
        return None
    buckets = defaultdict(float)
    for record in records:
        w = weight(record.get('weight'))
        if w is None:
            return None
        instrument = token(record.get('instrument_type'))
        currency = ('FUND_UNITS' if instrument.startswith('CF') or instrument in ('ETFA', 'ETFB', 'ETFC')
                    else normalize(record.get('quantity_unit')))
        buckets[currency] += w
    return dict(sorted(buckets.items()))
