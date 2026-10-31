"""Display/convert helpers. Money is stored as integer paise and tax/percent
as basis points; humans type rupees and percentages (both are x100)."""
from decimal import ROUND_HALF_UP, Decimal


def indian_group(n: int) -> str:
    """1234567 -> '12,34,567' (lakh/crore grouping)."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head] + pairs + [tail])


def format_rupees(paise):
    """Integer paise -> '₹12,34,567.89'. Display only; never used for arithmetic."""
    try:
        paise = int(paise)
    except (TypeError, ValueError):
        return ""
    sign = "-" if paise < 0 else ""
    whole, p = divmod(abs(paise), 100)
    return f"{sign}₹{indian_group(whole)}.{p:02d}"


def format_bps(bps):
    """1800 -> '18.00%'."""
    try:
        bps = int(bps)
    except (TypeError, ValueError):
        return ""
    whole, frac = divmod(abs(bps), 100)
    return f"{'-' if bps < 0 else ''}{whole}.{frac:02d}%"


def to_minor(value: Decimal) -> int:
    """Decimal rupees (or percent) -> integer paise (or bps), half-up. Exact: no floats."""
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_minor(minor: int) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))
