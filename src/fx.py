"""Date-aligned conversion with a bounded, backward-only observation lookup."""
from bisect import bisect_right
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import text
from src.db.engine import SessionLocal

MAX_AGE_DAYS = 7


class Rates:
    def __init__(self, rows):
        self.by_currency = {}
        for row in rows:
            self.by_currency.setdefault(row['currency'], []).append(dict(row))
        for currency, points in self.by_currency.items():
            points.sort(key=lambda p:p['date'])
        self.dates = {c:[p['date'] for p in points] for c,points in self.by_currency.items()}

    @classmethod
    def load(cls, start, end):
        if start is None or end is None:
            return cls([])
        with SessionLocal() as s:
            rows = s.execute(text('SELECT * FROM fx_daily WHERE date BETWEEN :start AND :end'),
                dict(start=start-timedelta(days=MAX_AGE_DAYS),end=end)).mappings().all()
        return cls(rows)

    def get(self, currency, day):
        if day is None:
            return None
        if currency == 'CLP':
            return dict(currency='CLP',date=day,clp_per_unit=Decimal(1),series_code=None)
        index = bisect_right(self.dates.get(currency,[]),day)-1
        if index < 0:
            return None
        row = self.by_currency[currency][index]
        return row if (day-row['date']).days <= MAX_AGE_DAYS else None
