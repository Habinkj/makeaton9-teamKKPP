"""Billing-period arithmetic. Pure functions: no database, no Django.

Periods are half-open: [start, next_start). Month-end handling uses the
subscription's anchor_day so a Jan 31 start bills Jan 31 -> Feb 28 -> Mar 31
(never drifting to the 28th forever).
"""
import calendar
from datetime import date, timedelta


def _add_months(d: date, months: int, anchor_day: int | None) -> date:
    year, month0 = divmod(d.year * 12 + (d.month - 1) + months, 12)
    month = month0 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor_day or d.day, last))


def next_period_start(current: date, period: str, anchor_day: int | None = None) -> date:
    if period == "daily":
        return current + timedelta(days=1)
    if period == "weekly":
        return current + timedelta(days=7)
    if period == "monthly":
        return _add_months(current, 1, anchor_day)
    if period == "yearly":
        return _add_months(current, 12, anchor_day)
    raise ValueError(f"Unknown billing period: {period}")


def periods_due(first_start: date, as_of: date, period: str, anchor_day: int | None = None,
                last_start: date | None = None, cap: int = 1000):
    """Yield every period start from first_start up to and including as_of
    (and last_start, if given)."""
    start, n = first_start, 0
    while start <= as_of and (last_start is None or start <= last_start) and n < cap:
        yield start
        start = next_period_start(start, period, anchor_day)
        n += 1
