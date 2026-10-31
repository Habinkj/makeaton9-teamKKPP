"""Billing engine (database side). The maths lives in billing_calc.compute();
this module decides WHAT to bill and writes it safely.

Safety properties:
  * Idempotent: a live invoice for (subscription, period_start) is never created
    twice. The partial unique constraint invoice_one_per_period is the backstop.
  * Each subscription bills in its own transaction; one failure doesn't stop the run.
  * Concurrent runs: rows are locked with SELECT ... FOR UPDATE SKIP LOCKED, so two
    runs (button + cron) never bill the same subscription at once.
  * Discount usage limits are reserved with a conditional UPDATE, so a limited
    discount can't be over-used even under concurrency.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid4

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from core.billing_calc import DiscountIn, LineIn, compute
from core.formatting import format_rupees
from core.models import Discount, Event, Invoice, InvoiceLine, Subscription
from core.periods import next_period_start
from core.statemachine import apply_transition

TERMS_DAYS = {"immediate": 0, "net15": 15, "net30": 30}
MAX_PERIODS_PER_RUN = 60      # catch-up cap per subscription per run


class BillingError(Exception):
    """A rule stopped an invoice action. Message is safe to show the user."""


@dataclass
class RunSummary:
    as_of: date
    dry_run: bool = False
    invoices_created: int = 0
    subscriptions_billed: int = 0
    skipped: list = field(default_factory=list)     # (sub number, reason)
    errors: list = field(default_factory=list)      # (sub number, message)
    auto_closed: list = field(default_factory=list)

    def as_text(self):
        return (f"As of {self.as_of}: {self.invoices_created} invoice(s) for "
                f"{self.subscriptions_billed} subscription(s); {len(self.skipped)} skipped; "
                f"{len(self.errors)} error(s); {len(self.auto_closed)} auto-closed.")


# --- what is due ------------------------------------------------------------

def due_subscriptions(as_of):
    """Active, not paused, next invoice date reached, not past expiry. Uses sub_due_idx."""
    return (Subscription.objects
            .filter(status="active", is_paused=False, next_invoice_date__lte=as_of)
            .filter(Q(expiry_date__isnull=True) | Q(next_invoice_date__lte=F("expiry_date"))))


def _line_inputs(sub):
    return [LineIn(l.pk, l.product_id,
                   f"{l.product.name}" + (f" ({l.variant.attribute}: {l.variant.value})" if l.variant_id else ""),
                   l.quantity, l.unit_price, l.tax_rate_bps)
            for l in sub.lines.select_related("product", "variant").order_by("id")]


def _discount_inputs(sub, on_date, exclude=()):
    eligible = (Discount.objects
                .filter(starts_on__lte=on_date)
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=on_date))
                .filter(Q(usage_limit__isnull=True) | Q(used_count__lt=F("usage_limit")))
                .exclude(pk__in=list(exclude)))
    product = [DiscountIn(d.pk, d.name, d.kind, d.value,
                          frozenset(p.pk for p in d.products.all()), d.min_purchase, d.min_quantity)
               for d in eligible.filter(scope="product").prefetch_related("products")]
    sub_disc = None
    if sub.discount_id:
        d = eligible.filter(pk=sub.discount_id, scope="subscription").first()
        if d:
            sub_disc = DiscountIn(d.pk, d.name, d.kind, d.value, frozenset(), d.min_purchase, d.min_quantity)
    return product, sub_disc


def _reserve(discount_ids):
    """Atomically count one use per discount. Returns the id that hit its limit, or None."""
    done = []
    for pk in discount_ids:
        ok = (Discount.objects.filter(pk=pk)
              .filter(Q(usage_limit__isnull=True) | Q(used_count__lt=F("usage_limit")))
              .update(used_count=F("used_count") + 1))
        if not ok:
            Discount.objects.filter(pk__in=done).update(used_count=F("used_count") - 1)
            return pk
        done.append(pk)
    return None


# --- creating one invoice ---------------------------------------------------

def _create_invoice(sub, period_start, actor=None):
    lines = _line_inputs(sub)
    excluded = set()
    while True:
        product, sub_disc = _discount_inputs(sub, period_start, excluded)
        result = compute(lines, product, sub_disc)
        hit_limit = _reserve(result.discount_ids)
        if hit_limit is None:
            break
        excluded.add(hit_limit)       # someone used the last slot; recompute without it

    period_end = next_period_start(period_start, sub.plan.billing_period, sub.anchor_day)
    inv = Invoice.objects.create(
        number=f"TMP-{uuid4().hex[:12]}", subscription=sub, customer=sub.customer,
        status="draft", period_start=period_start, period_end=period_end,
        issue_date=period_start,
        due_date=period_start + timedelta(days=TERMS_DAYS.get(sub.payment_terms, 0)),
        subtotal=result.subtotal, discount_total=result.discount_total,
        tax_total=result.tax_total, total=result.total,
        discount_applied_id=result.primary_discount_id, calc_trace=result.trace)
    inv.number = f"INV-{inv.pk:06d}"
    inv.save(update_fields=["number"])
    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=inv, product_id=o.product_id, description=o.description,
                    quantity=o.quantity, unit_price=o.unit_price,
                    discount_amount=o.discount_amount, tax_rate_bps=o.tax_rate_bps,
                    tax_amount=o.tax_amount, line_total=o.line_total)
        for o in result.lines])
    Event.objects.create(invoice=inv, from_status="", to_status="draft", actor=actor,
                         note=f"Generated for {period_start:%d %b %Y} – {period_end:%d %b %Y}")
    return inv, period_end


def bill_subscription(sub_pk, as_of, actor=None):
    """Bill every period due for one subscription, in ONE transaction.
    Returns (invoices_created, skip_reason_or_None, auto_closed)."""
    with transaction.atomic():
        sub = (Subscription.objects.select_for_update(skip_locked=True, of=("self",))
               .select_related("plan", "customer").filter(pk=sub_pk).first())
        if sub is None:
            return 0, "locked by another billing run", False
        if not sub.lines.exists():
            return 0, "no lines", False
        created = 0
        while (sub.status == "active" and not sub.is_paused and sub.next_invoice_date
               and sub.next_invoice_date <= as_of
               and (sub.expiry_date is None or sub.next_invoice_date <= sub.expiry_date)
               and created < MAX_PERIODS_PER_RUN):
            start = sub.next_invoice_date
            already = (Invoice.objects.filter(subscription=sub, period_start=start)
                       .exclude(status="cancelled").exists())
            if already:   # self-heal: invoice exists, schedule just wasn't advanced
                nxt = next_period_start(start, sub.plan.billing_period, sub.anchor_day)
            else:
                _, nxt = _create_invoice(sub, start, actor)
                created += 1
            sub.next_invoice_date = nxt
        sub.save(update_fields=["next_invoice_date"])

        closed = False
        if (sub.plan.auto_close and sub.status == "active" and sub.expiry_date
                and sub.expiry_date < as_of
                and (sub.next_invoice_date is None or sub.next_invoice_date > sub.expiry_date)):
            apply_transition(sub, "closed", actor=actor, note="Auto-closed at expiry")
            sub.next_invoice_date = None
            sub.save(update_fields=["next_invoice_date"])
            closed = True
        return created, None, closed


def _auto_close_candidates(as_of):
    """Expired auto-close subscriptions that have nothing left to bill."""
    return (Subscription.objects
            .filter(status="active", plan__auto_close=True, expiry_date__lt=as_of)
            .filter(Q(next_invoice_date__isnull=True) | Q(next_invoice_date__gt=F("expiry_date"))))


def run_billing(as_of, actor=None, limit=None):
    summary = RunSummary(as_of=as_of)
    pks = list(due_subscriptions(as_of).order_by("next_invoice_date", "pk").values_list("pk", flat=True))
    pks += list(_auto_close_candidates(as_of).values_list("pk", flat=True))
    if limit:
        pks = pks[:limit]
    numbers = dict(Subscription.objects.filter(pk__in=pks).values_list("pk", "number"))
    for pk in dict.fromkeys(pks):                       # de-duplicate, keep order
        try:
            created, reason, closed = bill_subscription(pk, as_of, actor)
        except Exception as e:                          # isolate: one bad sub never stops the run
            summary.errors.append((numbers.get(pk, pk), str(e)[:200]))
            continue
        if reason:
            summary.skipped.append((numbers.get(pk, pk), reason))
        if created:
            summary.invoices_created += created
            summary.subscriptions_billed += 1
        if closed:
            summary.auto_closed.append(numbers.get(pk, pk))
    return summary


def preview(as_of, limit=50):
    """Dry run: what WOULD be billed. Writes nothing."""
    qs = due_subscriptions(as_of).select_related("plan", "customer").order_by("next_invoice_date", "pk")
    rows, exceptions = [], []
    for sub in qs[:limit]:
        lines = _line_inputs(sub)
        if not lines:
            exceptions.append((sub, "no lines"))
            continue
        n, start = 0, sub.next_invoice_date
        while start <= as_of and (sub.expiry_date is None or start <= sub.expiry_date) and n < MAX_PERIODS_PER_RUN:
            n += 1
            start = next_period_start(start, sub.plan.billing_period, sub.anchor_day)
        product, sub_disc = _discount_inputs(sub, sub.next_invoice_date)
        first = compute(lines, product, sub_disc)
        rows.append({"sub": sub, "periods": n, "first_total": first.total,
                     "first_start": sub.next_invoice_date})
    paused = (Subscription.objects.filter(status="active", is_paused=True,
                                          next_invoice_date__lte=as_of).count())
    return {"due_count": qs.count(), "rows": rows, "exceptions": exceptions,
            "paused_count": paused, "auto_close_count": _auto_close_candidates(as_of).count()}


# --- invoice actions ------------------------------------------------------

def amount_paid(invoice):
    return invoice.payments.aggregate(s=Sum("amount"))["s"] or 0


def confirm_invoice(invoice, actor):
    apply_transition(invoice, "confirmed", actor=actor)
    return invoice


def cancel_invoice(invoice, actor, note=""):
    """Allowed on unpaid Draft/Confirmed. Releases the discount uses it consumed."""
    with transaction.atomic():
        if amount_paid(invoice) > 0:
            raise BillingError("This invoice has payments recorded, so it can't be cancelled.")
        apply_transition(invoice, "cancelled", actor=actor, note=note)   # table blocks paid/cancelled
        for pk in (invoice.calc_trace or {}).get("discounts_used", []):
            Discount.objects.filter(pk=pk, used_count__gt=0).update(used_count=F("used_count") - 1)
    return invoice


def send_invoice(invoice, actor):
    if invoice.status not in ("confirmed", "paid"):
        raise BillingError("Confirm the invoice before sending it.")
    user = invoice.customer.user
    if not user or not user.email:
        raise BillingError("This customer has no portal email address.")
    lines = "\n".join(
        f"  {l.description} x{l.quantity}: {format_rupees(l.line_total)}"
        for l in invoice.lines.all())
    body = (f"Hello {invoice.customer},\n\n"
            f"Invoice {invoice.number} for {invoice.period_start:%d %b %Y} – {invoice.period_end:%d %b %Y}\n\n"
            f"{lines}\n\n"
            f"Subtotal: {format_rupees(invoice.subtotal)}\n"
            f"Discount: -{format_rupees(invoice.discount_total)}\n"
            f"Tax:      {format_rupees(invoice.tax_total)}\n"
            f"Total:    {format_rupees(invoice.total)}\n"
            f"Due by {invoice.due_date:%d %b %Y}.\n\n— Brightloop Billing")
    send_mail(f"Brightloop invoice {invoice.number}", body, None, [user.email])
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=["sent_at"])
    Event.objects.create(invoice=invoice, from_status=invoice.status, to_status=invoice.status,
                         actor=actor, note=f"Emailed to {user.email}")
    return invoice
