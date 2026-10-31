"""Invoice maths as one pure function: lines + discounts in, numbers + trace out.

No database, no Django, no clock. That makes it exhaustively testable and the
trace explains every paisa.

Rules (locked):
  * Money is integer paise; tax rates and percent discounts are basis points.
  * Discount is applied BEFORE tax; tax is computed on the discounted amount,
    per line, rounded half-up exactly once.
  * Discounts never stack. Two candidate outcomes are compared:
      A) each line gets its single best product discount
      B) the one subscription-level discount applies across the whole invoice
    The invoice uses whichever saves the customer more (tie -> A).
"""
from dataclasses import dataclass


def rhu(numerator: int, denominator: int) -> int:
    """Round half up for non-negative integers."""
    return (2 * numerator + denominator) // (2 * denominator)


@dataclass(frozen=True)
class LineIn:
    key: int
    product_id: int
    description: str
    quantity: int
    unit_price: int          # paise, includes any variant extra (snapshot)
    tax_rate_bps: int        # snapshot


@dataclass(frozen=True)
class DiscountIn:
    id: int
    name: str
    kind: str                # "percent" | "fixed"
    value: int               # bps if percent, paise if fixed
    product_ids: frozenset = frozenset()
    min_purchase: int = 0    # paise
    min_quantity: int = 0


@dataclass
class LineOut:
    key: int
    product_id: int
    description: str
    quantity: int
    unit_price: int
    gross: int
    discount_amount: int
    discount_id: int | None
    taxable: int
    tax_rate_bps: int
    tax_amount: int
    line_total: int


@dataclass
class Result:
    lines: list
    subtotal: int
    discount_total: int
    tax_total: int
    total: int
    discount_ids: list           # distinct discounts actually applied (usage is counted per invoice)
    primary_discount_id: int | None
    trace: dict


def _amount(d: DiscountIn, base: int) -> int:
    return rhu(base * d.value, 10000) if d.kind == "percent" else min(d.value, base)


def _allocate(total: int, weights: list) -> list:
    """Split `total` paise across weights, exactly, by largest remainder."""
    s = sum(weights)
    base = [total * w // s for w in weights]
    leftover = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % s), i))
    for i in order[:leftover]:
        base[i] += 1
    return base


def compute(lines, product_discounts=(), subscription_discount=None) -> Result:
    gross = [l.quantity * l.unit_price for l in lines]
    subtotal = sum(gross)
    total_qty = sum(l.quantity for l in lines)

    # A) best single product discount per line
    a_amounts, a_ids = [], []
    for l, g in zip(lines, gross):
        best_amt, best_id = 0, None
        for d in sorted(product_discounts, key=lambda d: d.id):
            if l.product_id in d.product_ids and l.quantity >= d.min_quantity and g >= d.min_purchase:
                amt = _amount(d, g)
                if amt > best_amt:
                    best_amt, best_id = amt, d.id
        a_amounts.append(best_amt)
        a_ids.append(best_id)
    total_a = sum(a_amounts)

    # B) one subscription-level discount over the whole invoice
    sd, b_amounts, total_b = subscription_discount, None, 0
    if sd and subtotal > 0 and total_qty >= sd.min_quantity and subtotal >= sd.min_purchase:
        if sd.kind == "percent":
            b_amounts = [rhu(g * sd.value, 10000) for g in gross]
        else:
            b_amounts = _allocate(min(sd.value, subtotal), gross)
        total_b = sum(b_amounts)

    if b_amounts is not None and total_b > total_a:
        chosen, amounts, ids = "subscription", b_amounts, [sd.id] * len(lines)
    elif total_a > 0:
        chosen, amounts, ids = "product", a_amounts, a_ids
    else:
        chosen, amounts, ids = "none", [0] * len(lines), [None] * len(lines)

    out, trace_lines = [], []
    for l, g, disc, did in zip(lines, gross, amounts, ids):
        taxable = g - disc                                   # discount first ...
        tax = rhu(taxable * l.tax_rate_bps, 10000)           # ... then tax, rounded once
        out.append(LineOut(l.key, l.product_id, l.description, l.quantity, l.unit_price, g,
                           disc, did if disc else None, taxable, l.tax_rate_bps, tax, taxable + tax))
        trace_lines.append({
            "line": l.description, "qty": l.quantity, "unit_price": l.unit_price, "gross": g,
            "discount": disc, "discount_id": did if disc else None, "taxable": taxable,
            "tax_rate_bps": l.tax_rate_bps, "tax": tax, "line_total": taxable + tax})

    discount_total = sum(o.discount_amount for o in out)
    tax_total = sum(o.tax_amount for o in out)
    applied = sorted({o.discount_id for o in out if o.discount_id})
    saving = {i: sum(o.discount_amount for o in out if o.discount_id == i) for i in applied}
    primary = max(applied, key=lambda i: (saving[i], -i)) if applied else None

    return Result(
        lines=out, subtotal=subtotal, discount_total=discount_total, tax_total=tax_total,
        total=subtotal - discount_total + tax_total, discount_ids=applied,
        primary_discount_id=primary,
        trace={
            "rules": "discount before tax; tax per line, rounded half-up once; no stacking",
            "subtotal": subtotal, "option_a_product_discounts": total_a,
            "option_b_subscription_discount": total_b, "chosen": chosen,
            "discounts_used": applied, "lines": trace_lines,
            "discount_total": discount_total, "tax_total": tax_total,
            "total": subtotal - discount_total + tax_total,
        })
