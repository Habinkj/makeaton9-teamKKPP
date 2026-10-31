"""Pure helpers for dashboard figures (no database)."""
from core.billing_calc import rhu

# Multiply, then divide, to turn a per-period amount into a per-month amount.
MONTHLY_FACTOR = {"monthly": (1, 1), "yearly": (1, 12), "weekly": (52, 12), "daily": (365, 12)}


def to_monthly(period: str, amount: int) -> int:
    num, den = MONTHLY_FACTOR[period]
    return rhu(amount * num, den)


def mrr(rows) -> int:
    """rows: iterable of (billing_period, recurring amount per period in paise)."""
    return sum(to_monthly(period, amount or 0) for period, amount in rows)
